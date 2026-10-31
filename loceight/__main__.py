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

    d = sub.add_parser("devices", help="list the Pods on an account (to find device_id values)")
    d.add_argument("account", nargs="?", help="account key from config (default: all)")

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
        keys = [args.account] if args.account else list(cfg.accounts)
        for key in keys:
            acct = cfg.accounts[key]
            me = EightSleep(acct.email, acct.password).me()
            print(f"{key} ({acct.email}):")
            print(f"  devices: {me.get('devices')}")
            print(f"  currentDevice: {me.get('currentDevice')}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
