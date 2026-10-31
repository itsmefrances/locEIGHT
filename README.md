# locEIGHT

Automatically switches your Eight Sleep beds to match where you are, for households with an Eight Sleep Pod in more than one home.

## What it does

- **The home you're closer to is "home."** Its Pod is switched on for **everyone who sleeps on it**: both sides, from one Eight Sleep login. On the drive between homes, the switch happens partway there, so the bed has time to reach temperature before you arrive.
- **When you're more than 50 miles from every home**, everyone is set to **away**.
- **Pods you don't list are never touched**, such as a guest bed.
- **Each change happens once per trip.** It doesn't keep re-applying settings. If you change something by hand in the Eight Sleep app, it stays that way until your next trip.

Only one phone is needed. Everyone else on the beds follows that phone's location.

## How it works

```
iPhone Shortcut ── "I'm at lat, lon" ──► small web service (free on Render) ──► Eight Sleep
(nightly + along                        decides which home is closer and
 the drive)                             switches the beds only when that changes
```

---

## Set it up for your own homes

You'll need:
- an Eight Sleep account with a Pod at each home;
- an iPhone;
- a free [GitHub](https://github.com) account;
- a free [Render](https://render.com) account.

It takes about 20 minutes. **Easiest option:** open your copy of this project in [Claude Code](https://claude.ai/code) and say "set this up for my Eight Sleep beds". It can do steps 2–4 for you. The steps below are for doing it yourself.

### 1. Get your own copy of the code

On this project's GitHub page, click **Fork** (or **Use this template**) to make a copy in your own GitHub account.

### 2. Put it online with Render

1. In Render, click **New → Blueprint** and choose your copy of this project. Render reads `render.yaml` and creates two pieces, both on the free plan:
   - the web service;
   - a small memory store that remembers which home you're at.
2. Render will ask for two values:
   - **`EIGHT_PASSWORD`**: your Eight Sleep password.
   - **`LOCEIGHT_CONFIG_JSON`**: paste the text below. Put your Eight Sleep email in it, and the latitude and longitude of each home. To get a home's coordinates, long-press the spot in Google Maps and copy the numbers. Leave `device_id` empty for now.

   ```json
   {"account":{"email":"you@example.com","password_env":"EIGHT_PASSWORD"},
    "beds":[{"name":"City","lat":40.7128,"lon":-74.0060,"device_id":""},
            {"name":"Country","lat":41.9000,"lon":-74.0000,"device_id":""}]}
   ```

3. Click **Apply**. Wait for the service to show **Live**.

### 3. Tell it which Pod is which

1. In Render, open the service and go to **Logs**. Look for lines like:

   ```
   Set 'Country Pod' setId=… devices=['3d9b…']
   Pod device_id=3d9b… sleepers={'left': 'Alex', 'right': 'Sam'}
   ```

   These show every Pod on your account, with the names you gave them in the Eight Sleep app.
2. Copy each home's `device_id` into `LOCEIGHT_CONFIG_JSON`. Go to **Environment**, edit the value, and save. Leave out any Pod you don't want managed, such as a guest bed.
3. While you're on the **Environment** page, copy the value of **`LOCEIGHT_TOKEN`**. This is the private passcode your phone will use.

### 4. Add the iPhone shortcut

1. In the **Shortcuts** app, tap **+** and name the shortcut **Update Beds**.
2. Add **Get Current Location**.
3. Add **Get Contents of URL**. Tap the URL and enter, using your service's address from Render:
   `https://YOUR-SERVICE.onrender.com/location?token=YOUR_TOKEN&lat=`
4. Insert **Current Location**, tap it, and choose **Latitude**.
5. Type `&lon=`, insert **Current Location** again, and choose **Longitude**.
6. Run the shortcut once. In the Eight Sleep app, the Pod at the home you're closer to should be the active one.

### 5. Make it run by itself

In Shortcuts, go to **Automation → +**. For each automation below, choose **Run Immediately** and **Update Beds**:

- **Charger → Is Connected.** This runs every night when you plug in your phone.
- **Arrive → a spot on your usual route about 30–40 minutes before each home.** Drag the circle as large as it goes. This is what switches the beds early enough to reach temperature. iPhone arrival alerts can be slow at highway speed, which is why the circle should be big. Passing a spot in the other direction does nothing, because the service always picks the closer home.

Running it often is fine. It only acts when you've actually moved between homes or left them all.

---

## Technical details

### How it decides

- The nearest bed is "home" as long as it's within `away_miles` (50 by default). Make sure every point on the drive between your homes is within this distance of one of them. Otherwise the drive itself counts as away. For homes up to about 100 miles apart, the default works.
- To avoid flip-flopping near the halfway point, a home bed keeps that status until another bed is `switch_margin_miles` (10) closer.
- Once you're home, you only count as away beyond `away_miles + hysteresis_miles` (55) from every bed.
- It remembers the last area you were in: near bed A, near bed B, or near none. It only calls Eight Sleep when that changes.
  - **Arriving near a bed:** it reads who sleeps on the left and right of each listed Pod. For each person, it makes that Pod's household set the active one, then turns away mode off. Anyone without a side on that Pod is set to away. If the household call is refused, it falls back to the older current-device call.
  - **Far from every bed:** everyone is set to away.
- If a call fails, the area isn't recorded, so the next update retries it.
- The memory lives in Redis when `REDIS_URL` is set (the free Render Key Value instance in `render.yaml`). Otherwise it uses a local `state.json` file. Hosting platforms wipe local files on restart, which would re-apply the last change.

### Config

See `config.example.json`.

| Key | Default | Meaning |
|---|---|---|
| `away_miles` | 50 | Farther than this from every bed counts as away |
| `switch_margin_miles` | 10 | How much closer another bed must be before switching to it |
| `hysteresis_miles` | 5 | Extra distance before going from home to away |
| `when_away_from_all` | `"away"` | `"away"` or `"unchanged"` when you're near no bed |

Environment variables:

- `LOCEIGHT_CONFIG_JSON`: the config.
- `EIGHT_PASSWORD`: the Eight Sleep password that `password_env` points to.
- `LOCEIGHT_TOKEN`: the private passcode the phone sends.
- `REDIS_URL`: where the memory is kept.

At startup, the server logs every Pod, which household set it's in, and who sleeps on each side, as long as any bed is missing its `device_id`.

### Commands

Python 3.10+. No dependencies beyond the standard library.

```bash
python -m loceight devices                        # list Pods and their device IDs
python -m loceight check 40.75 -73.98 --dry-run   # show what a location would do
python -m loceight check 40.75 -73.98 --force     # re-apply even if nothing changed
python -m loceight status
python -m loceight serve                          # web service (needs LOCEIGHT_TOKEN)
python -m unittest discover -s tests
```

### Endpoints

Every endpoint except `/healthz` needs `?token=<LOCEIGHT_TOKEN>`.

- `GET /location?token=..&lat=..&lon=..` applies a location. The iPhone shortcut calls this. Add `&dry_run=1` to preview without changing anything.
- `GET /status?token=..` shows the last known home, distances and any errors.
- `GET /healthz` is a liveness check.

### Caveat

Eight Sleep has no public API. This uses the same endpoints as the Eight Sleep app. They're documented by two open-source projects: [pyEight](https://github.com/lukas-clarke/pyEight), the library behind the Home Assistant integration, and [eightctl](https://github.com/steipete/eightctl). Eight Sleep may change them at any time.
