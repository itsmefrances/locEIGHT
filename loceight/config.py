"""Configuration loading.

Config is JSON, read from (first match wins):
  1. the path passed on the command line
  2. $LOCEIGHT_CONFIG_JSON (the JSON itself, handy on hosting platforms)
  3. $LOCEIGHT_CONFIG (a path)
  4. ./config.json
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


class ConfigError(ValueError):
    pass


@dataclass
class Account:
    key: str
    email: str
    password: str


@dataclass
class Bed:
    name: str
    lat: float
    lon: float
    account: str
    device_id: str | None = None
    side: str = "solo"


@dataclass
class Config:
    radius_miles: float
    hysteresis_miles: float
    when_away_from_all: str  # "away" or "unchanged"
    accounts: dict[str, Account]
    beds: list[Bed]
    state_file: str = "state.json"
    max_accuracy_miles: float | None = None
    beds_by_account: dict[str, list[Bed]] = field(default_factory=dict)


def _password(key: str, raw: dict) -> str:
    if "password_env" in raw:
        var = raw["password_env"]
        value = os.environ.get(var)
        if not value:
            raise ConfigError(f"account '{key}': environment variable {var} is not set")
        return value
    if "password" in raw:
        return raw["password"]
    raise ConfigError(f"account '{key}': set 'password_env' (recommended) or 'password'")


def parse(data: dict) -> Config:
    try:
        accounts = {}
        for key, raw in data["accounts"].items():
            accounts[key] = Account(key=key, email=raw["email"], password=_password(key, raw))

        beds = []
        for raw in data["beds"]:
            bed = Bed(
                name=raw["name"],
                lat=float(raw["lat"]),
                lon=float(raw["lon"]),
                account=raw["account"],
                device_id=raw.get("device_id"),
                side=raw.get("side", "solo"),
            )
            if bed.account not in accounts:
                raise ConfigError(f"bed '{bed.name}' uses unknown account '{bed.account}'")
            if bed.side not in ("solo", "left", "right"):
                raise ConfigError(f"bed '{bed.name}': side must be solo, left or right")
            beds.append(bed)
    except KeyError as e:
        raise ConfigError(f"missing required config key: {e}") from None

    if len({b.name for b in beds}) != len(beds):
        raise ConfigError("bed names must be unique")

    by_account: dict[str, list[Bed]] = {}
    for bed in beds:
        by_account.setdefault(bed.account, []).append(bed)
    for key, group in by_account.items():
        if len(group) > 1:
            missing = [b.name for b in group if not b.device_id]
            if missing:
                raise ConfigError(
                    f"beds {missing} share account '{key}', so each needs a 'device_id' "
                    "(run `python -m loceight devices` to find them)"
                )

    when = data.get("when_away_from_all", "away")
    if when not in ("away", "unchanged"):
        raise ConfigError("when_away_from_all must be 'away' or 'unchanged'")

    max_acc = data.get("max_accuracy_miles")
    return Config(
        radius_miles=float(data.get("radius_miles", 30)),
        hysteresis_miles=float(data.get("hysteresis_miles", 5)),
        when_away_from_all=when,
        accounts=accounts,
        beds=beds,
        state_file=os.environ.get("LOCEIGHT_STATE_FILE", data.get("state_file", "state.json")),
        max_accuracy_miles=float(max_acc) if max_acc is not None else None,
        beds_by_account=by_account,
    )


def load(path: str | None = None) -> Config:
    if path:
        with open(path) as f:
            return parse(json.load(f))
    inline = os.environ.get("LOCEIGHT_CONFIG_JSON")
    if inline:
        return parse(json.loads(inline))
    with open(os.environ.get("LOCEIGHT_CONFIG", "config.json")) as f:
        return parse(json.load(f))
