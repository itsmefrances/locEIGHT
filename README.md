# locEIGHT

Automatically switches your Eight Sleep beds to match where you are.

## What it does

- **The home you're closer to is "home."** Its bed is switched on for **both you and Dani**. On the drive between the homes, the switch happens about halfway, so the bed you're heading to is ready when you arrive.
- **When you're more than 50 miles from both homes** (a real trip, not the drive between them), you and Dani are both set to **away**.
- **The guest bed is never touched.**
- **Each change happens once, when you travel.** It doesn't keep checking or re-applying. If you change something by hand in the Eight Sleep app (for example, if you and Dani are apart for a night), it stays that way until your next trip.

It goes by your phone's location only. Dani's side follows yours. One Eight Sleep login controls both sides.

## Getting it running

| Step | Who | What |
|---|---|---|
| 1 | You | ✅ Done: home addresses and Eight Sleep email received. |
| 2 | Claude | Put the service online and connect it to Eight Sleep. |
| 3 | You | Enter the Eight Sleep password on the hosting site, in a field Claude points you to. It's never stored in this code. |
| 4 | Claude | Find the device ID of each bed and check that everything lines up. |
| 5 | You | Add one shortcut and two automations to your iPhone (below). It takes about 5 minutes. |

### iPhone setup (step 5)

**Create the shortcut.** Open the **Shortcuts** app, tap **+**, and name it **Update Beds**. Add these two actions:

1. **Get Current Location**
2. **Get Contents of URL**. Claude will give you the exact web address and password to paste in.

**Make it run automatically.** Go to the **Automation** tab and tap **+**. Create these two automations, choosing **Run Immediately** for each:

- **Charger → Is Connected → Update Beds.** This runs every night when you plug in your phone.
- **Arrive → a spot about 30–40 minutes before each home → Update Beds.** Drag the circle as large as it goes. This switches the beds early enough for them to reach temperature. Good spots are Thruway exit 17 (Newburgh) on the way to Accord, and the Mario Cuomo Bridge or Thruway exit 15 (Suffern) on the way to NYC. Because the service always picks the closer home, passing a spot in the other direction does nothing.

That's it. Repeated runs in the same place do nothing, so it's fine that these fire often.

---

## Technical details

### How it decides

- The nearest bed is "home" as long as it's within `away_miles` (50). The two homes are about 71 miles apart in a straight line, so every point on the drive is within about 40 miles of one of them. That means the drive never counts as away.
- To avoid flip-flopping near the halfway point, a bed that is already home keeps that status until the other bed is `switch_margin_miles` (10) closer.
- Once a bed is home, you're only counted as away when you're more than `away_miles + hysteresis_miles` (55) from every bed.
- It remembers the last area you were in: near bed A, near bed B, or near neither. It only calls Eight Sleep when that area changes.
  - **Arriving near a bed:** one login controls everyone on the managed Pods. The left and right sides are read from each Pod. For each person, it makes that bed their active Pod on their side and turns away mode off. Anyone without a side on that bed is set to away.
  - **Far from both:** it turns away mode on for everyone.
- If a call fails, the area isn't recorded, so the next update retries it.
- The memory lives in Redis when `REDIS_URL` is set (a free Render Key Value instance in `render.yaml`). Otherwise it uses a local `state.json` file. Hosting platforms wipe local files on restart, which would cause the change to be re-applied.

### Config

See `config.example.json`. Only the beds you list are managed, so leave the guest bed out. To find the `device_id` values, run `python -m loceight devices`. The server also logs them at startup if any are missing. The log shows who sleeps on each side, which makes the guest bed easy to spot.

| Key | Default | Meaning |
|---|---|---|
| `away_miles` | 50 | Farther than this from every bed counts as away |
| `switch_margin_miles` | 10 | How much closer the other bed must be before switching to it |
| `hysteresis_miles` | 5 | Extra distance before going from home to away |
| `when_away_from_all` | `"away"` | `"away"` or `"unchanged"` when you're near no bed |

Environment variables:

- `LOCEIGHT_CONFIG_JSON`: the config itself.
- `EIGHT_PASSWORD`: the Eight Sleep password that `password_env` points to.
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

Every endpoint except `/healthz` needs `?token=<LOCEIGHT_TOKEN>`.

- `GET /location?token=..&lat=..&lon=..` applies a location. The iPhone shortcut calls this. Add `&dry_run=1` to preview without changing anything.
- `GET /status?token=..` shows the last known home, distances and any errors.
- `GET /healthz` is a liveness check.

### Caveat

Eight Sleep has no public API. This uses the same endpoints as the mobile app, as documented by [pyEight](https://github.com/lukas-clarke/pyEight), the library behind the Home Assistant integration. Eight Sleep may change these endpoints at any time.
