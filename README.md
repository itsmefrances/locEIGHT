# locEIGHT

Automatically switches your Eight Sleep beds to match where you are.

## What it does

- **When you arrive within 30 miles of a home**, that home's bed is switched on for **both you and Dani**.
- **When you're more than 30 miles from both homes**, you and Dani are both set to **away**.
- **The guest bed is never touched.**
- **Each change happens once, when you travel.** It doesn't keep checking or re-applying. If you change something by hand in the Eight Sleep app (for example, if you and Dani are apart for a night), it stays that way until your next trip.

It goes by your phone's location only. Dani's settings follow yours.

## Getting it running

| Step | Who | What |
|---|---|---|
| 1 | You | Send the addresses of the two homes (not the guest bed). |
| 2 | Claude | Put the service online and connect it to Eight Sleep. |
| 3 | You | Enter your and Dani's Eight Sleep passwords on the hosting site, in fields Claude points you to. They're never stored in this code. |
| 4 | Claude | Find the device ID of each bed and check that everything lines up. |
| 5 | You | Add one shortcut and two automations to your iPhone (below). It takes about 5 minutes. |

### iPhone setup (step 5)

**Create the shortcut.** Open the **Shortcuts** app, tap **+**, and name it **Update Beds**. Add these two actions:

1. **Get Current Location**
2. **Get Contents of URL**. Claude will give you the exact web address and password to paste in.

**Make it run automatically.** Go to the **Automation** tab and tap **+**. Create these two automations, choosing **Run Immediately** for each:

- **Charger → Is Connected → Update Beds.** This runs every night when you plug in your phone.
- **Arrive → (each home) → Update Beds.** This is optional and switches the beds as you pull in.

That's it. Repeated runs in the same place do nothing, so it's fine that these fire often.

---

## Technical details

### How it decides

- A bed is "home" when you're within `radius_miles` (30). Once a bed is home, it stays home until you're more than `radius_miles + hysteresis_miles` (35) away. That way, hovering near the boundary doesn't flip the beds.
- It remembers the last area you were in: near bed A, near bed B, or near neither. It only calls Eight Sleep when that area changes.
  - **Arriving near a bed:** for each user, it makes that bed the account's active Pod, using the side that user is assigned to on that Pod, then turns away mode off.
  - **Far from both:** it turns away mode on for each user.
- If a call fails, the area isn't recorded, so the next update retries it.
- The memory lives in Redis when `REDIS_URL` is set (a free Render Key Value instance in `render.yaml`). Otherwise it uses a local `state.json` file. Hosting platforms wipe local files on restart, which would cause the change to be re-applied.

### Config

See `config.example.json`. Only the beds you list are managed; leave the guest bed out. Get the `device_id` values with `python -m loceight devices`. The server also logs them at startup if any are missing. A user's side on each Pod is detected automatically. To override it, add `"sides": {"City": "left"}` to that user.

| Key | Default | Meaning |
|---|---|---|
| `radius_miles` | 30 | How close counts as "at" a bed |
| `hysteresis_miles` | 5 | Extra distance before leaving a bed's area |
| `when_away_from_all` | `"away"` | `"away"` or `"unchanged"` when you're near no bed |
| `max_accuracy_miles` | none | Ignore location readings less accurate than this |

Environment variables:

- `LOCEIGHT_CONFIG_JSON`: the config itself.
- `EIGHT_PASSWORD_*`: the passwords that `password_env` points to.
- `LOCEIGHT_TOKEN`: the shared secret the phone sends.
- `REDIS_URL`: where the memory is kept.

### Commands

```bash
python -m loceight devices                        # list Pods and their device IDs
python -m loceight check 40.75 -73.98 --dry-run   # show what a location would do
python -m loceight check 40.75 -73.98 --force     # re-apply even if nothing changed
python -m loceight status
python -m loceight serve                          # webhook server (needs LOCEIGHT_TOKEN)
python -m unittest discover -s tests
```

### Endpoints

All endpoints except `/healthz` need the token. Send it as `Authorization: Bearer <token>`, as the password in basic auth, or as `?token=`.

- `POST /location` takes `{"lat": .., "lon": ..}`. OwnTracks payloads are also accepted. Add `?dry_run=1` to preview without changing anything.
- `GET /location?lat=..&lon=..` does the same thing.
- `GET /status`
- `GET /healthz`

### Caveat

Eight Sleep has no public API. This uses the same endpoints as the mobile app, as documented by [pyEight](https://github.com/lukas-clarke/pyEight), the library behind the Home Assistant integration. Eight Sleep may change these endpoints at any time.
