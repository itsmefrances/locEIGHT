"""Command line entry point: `python -m loceight <command>`."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from . import config as config_mod
from .controller import Controller
from .eightsleep import EightSleep


def log_devices(cfg) -> None:
    """Log every Pod in the household and who sleeps on each side, so the
    device_id values can be filled in (and the guest bed left out)."""
    from .controller import side_assignments

    log = logging.getLogger("loceight")
    try:
        client = EightSleep(cfg.account.email, cfg.account.password)
        current = (client.me().get("currentDevice") or {}).get("id")
        try:
            summary = client.household_summary()
            for household in summary.get("households", []):
                for pod_set in household.get("sets", []):
                    log.info("Set %r setId=%s devices=%s", pod_set.get("setName"), pod_set.get("setId"),
                             [d.get("deviceId") for d in pod_set.get("devices", []) if isinstance(d, dict)])
            log.info("Active set: %s", client.household_current_set().get("setId"))
            ids = client.household_device_ids()
        except Exception as e:
            log.error("could not read household (%s); falling back to users/me", e)
            ids = []
        if not ids:
            ids = [d if isinstance(d, str) else d.get("id") for d in client.me().get("devices") or []]
        for dev_id in ids:
            try:
                info = client.device(dev_id)
            except Exception as e:
                log.info("Pod device_id=%s (details unavailable: %s)", dev_id, e)
                continue
            people = {side: client.user_name(uid) for uid, side in side_assignments(info).items()}
            log.info(
                "Pod device_id=%s%s sleepers=%s online=%s lastHeard=%s",
                dev_id,
                " (your active Pod)" if dev_id == current else "",
                people or "-",
                info.get("online"),
                info.get("lastHeard"),
            )
    except Exception as e:
        log.error("could not list Pods: %s", e)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="loceight", description="Switch Eight Sleep beds by location.")
    p.add_argument("-c", "--config", help="path to config.json")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the webhook server")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)))

    c = sub.add_parser("check", help="apply a location once")
    c.add_argument("lat", type=float)
    c.add_argument("lon", type=float)
    c.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")
    c.add_argument("--force", action="store_true", help="resend even if state says nothing changed")

    sub.add_parser("status", help="print saved state")

    sub.add_parser("devices", help="list the Pods on each account (to find device_id values)")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        cfg = config_mod.load(args.config)
    except (OSError, config_mod.ConfigError, json.JSONDecodeError) as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    ctl = Controller(cfg)

    if args.cmd == "serve":
        token = os.environ.get("LOCEIGHT_TOKEN")
        if not token or len(token) < 16:
            print("set LOCEIGHT_TOKEN to a random secret of at least 16 characters", file=sys.stderr)
            return 2
        from .server import serve

        if any(not b.device_id for b in cfg.beds):
            log_devices(cfg)
        serve(ctl, token, args.host, args.port)
        return 0

    if args.cmd == "check":
        result = ctl.update(args.lat, args.lon, dry_run=args.dry_run, force=args.force)
        print(json.dumps(result.to_dict(), indent=2))
        return 1 if result.errors else 0

    if args.cmd == "status":
        print(json.dumps(ctl.status(), indent=2))
        return 0

    if args.cmd == "devices":
        log_devices(cfg)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
