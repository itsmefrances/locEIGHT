# locEIGHT

Automatically switch your Eight Sleep Pods between **home** and **away** based on where you are.

> Within 30 miles of one bed → that bed is home and the other is set to away.
> Far from both → both are set to away (you can turn this off).

```
 iPhone (Shortcuts or OwnTracks)
        │  POST /location {lat, lon}
        ▼
 locEIGHT server ── which bed is within radius_miles? ──► Eight Sleep API
                                                           (away mode on/off,
                                                            active Pod)
```

No dependencies beyond the Python 3.10+ standard library.

## How it decides

1. It measures the distance from you to each bed.
2. A bed within `radius_miles` (default 30) counts as **home**. If two radii overlap, the nearest bed wins.
3. Once a bed is home, it stays home until you are more than `radius_miles + hysteresis_miles` away (default 35 mi). That way, sitting near the edge of the radius doesn't flip the beds back and forth.
4. Every other bed is set to **away**. If no bed is within range, `when_away_from_all` decides what happens:
   - `"away"` (default): every bed is set to away.
   - `"unchanged"`: the beds stay as they are.
5. It only calls Eight Sleep when something changes. If a call fails, it tries again on the next location update.

## Setup

### 1. Configure

```bash
cp config.example.json config.json
```

Edit `config.json`. Set each bed's latitude and longitude: in Google Maps, long-press the spot and copy the coordinates.

**One Eight Sleep account for both Pods** (the usual setup): Eight Sleep's away mode is per account, not per Pod. So locEIGHT also switches which Pod the account is active on. Each bed then needs its `device_id`. To find them, run:

```bash
export EIGHT_PASSWORD='your-eight-sleep-password'
python -m loceight devices
```

**A separate account for each Pod:** add one entry per account under `accounts` and point each bed at its own account. `device_id` is optional in this setup.

Passwords are read from environment variables (`password_env`), so they never go in the file.

### 2. Try it

```bash
python -m loceight check 40.75 -73.98 --dry-run   # show what would happen
python -m loceight check 40.75 -73.98             # actually do it
python -m loceight status
```

### 3. Run the server

```bash
export LOCEIGHT_TOKEN=$(python -c 'import secrets; print(secrets.token_urlsafe(24))')
python -m loceight serve --port 8080
```

The server must be reachable from your phone. It can run on a home server or Raspberry Pi behind Tailscale or Cloudflare Tunnel, or on any small host. A `render.yaml` is included for Render. In Render's dashboard:

- Paste the contents of `config.json` into `LOCEIGHT_CONFIG_JSON`.
- Set `EIGHT_PASSWORD`.
- Copy the generated `LOCEIGHT_TOKEN`.

The state file on Render's free tier is wiped on each restart. That's harmless: the next update simply resends the current settings.

### 4. Send your location from your phone

**iPhone, Shortcuts app.** First create a shortcut named *Update Beds*:

1. **Get Current Location**
2. **Get Contents of URL**
   - URL: `https://YOUR-SERVER/location`
   - Method: `POST`
   - Headers: `Authorization` = `Bearer YOUR_TOKEN`
   - Request Body: JSON, with `lat` = *Current Location → Latitude* and `lon` = *Current Location → Longitude*

Then, under **Automation**, run *Update Beds* when:

- you arrive at or leave each home;
- you connect to a charger, which catches bedtime;
- optionally, at a few set times of day.

For each automation, choose **Run Immediately**.

**OwnTracks (iOS or Android):** for continuous background updates, use HTTP mode:

- URL: `https://YOUR-SERVER/location`
- Authentication on, with any username and your token as the password.

locEIGHT understands OwnTracks payloads directly.

## Endpoints

| Method | Path | |
|---|---|---|
| `POST` | `/location` | `{"lat": .., "lon": .., "accuracy_m": ..}` (or an OwnTracks payload). Add `?dry_run=1` to only preview. |
| `GET` | `/location?lat=..&lon=..&token=..` | Same, handy for testing from a browser |
| `GET` | `/status` | Last location, distances, which bed is home, and the last errors |
| `GET` | `/healthz` | No auth needed |

Auth: `Authorization: Bearer <token>`, basic auth with the token as the password, or `?token=`.

## Config reference

| Key | Default | Meaning |
|---|---|---|
| `radius_miles` | 30 | How close counts as "at" a bed |
| `hysteresis_miles` | 5 | Extra distance before a home bed flips back to away |
| `when_away_from_all` | `"away"` | `"away"` or `"unchanged"` when you're near no bed |
| `max_accuracy_miles` | none | Ignore location fixes less accurate than this |
| `state_file` | `state.json` | Where it remembers what it last sent (or set `LOCEIGHT_STATE_FILE`) |

## Caveat

Eight Sleep has no public API. locEIGHT uses the same endpoints as the mobile app, as documented by [pyEight](https://github.com/lukas-clarke/pyEight), the library behind the Home Assistant integration. Eight Sleep can change these endpoints at any time.

## Tests

```bash
python -m unittest discover -s tests
```
