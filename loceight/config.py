"""Configuration loading.

Config is JSON, read from (first match wins):
  1. the path passed on the command line
  2. $LOCEIGHT_CONFIG_JSON (the JSON itself, handy on hosting platforms)
  3. $LOCEIGHT_CONFIG (a path)
  4. ./config.json

Only the beds listed are managed; any other Pod on the account (e.g. a guest
bed) is never touched.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

SIDES = ("solo", "left", "right")


class ConfigError(ValueError):
    pass


@dataclass
class User:
    name: str
    email: str
    password: str
    sides: dict[str, str] = field(default_factory=dict)  # optional per-bed side override


@dataclass
class Bed:
    name: str
    lat: float
    lon: float
    device_id: str | None = None


@dataclass
class Config:
    away_miles: float
    switch_margin_miles: float
    hysteresis_miles: float
    when_away_from_all: str  # "away" or "unchanged"
    users: list[User]
    beds: list[Bed]
    state_file: str = "state.json"
    redis_url: str | None = None
    max_accuracy_miles: float | None = None


def _password(name: str, raw: dict) -> str:
    if "password_env" in raw:
        var = raw["password_env"]
        value = os.environ.get(var)
        if not value:
            raise ConfigError(f"user '{name}': environment variable {var} is not set")
        return value
    if "password" in raw:
        return raw["password"]
    raise ConfigError(f"user '{name}': set 'password_env' (recommended) or 'password'")


def parse(data: dict) -> Config:
    try:
        beds = [
            Bed(
                name=raw["name"],
                lat=float(raw["lat"]),
                lon=float(raw["lon"]),
                device_id=raw.get("device_id") or None,
            )
            for raw in data["beds"]
        ]
        bed_names = {b.name for b in beds}
        if len(bed_names) != len(beds):
            raise ConfigError("bed names must be unique")

        users = []
        for raw in data["users"]:
            name = raw.get("name") or raw["email"]
            sides = raw.get("sides", {})
            for bed_name, side in sides.items():
                if bed_name not in bed_names:
                    raise ConfigError(f"user '{name}': 'sides' names unknown bed '{bed_name}'")
                if side not in SIDES:
                    raise ConfigError(f"user '{name}': side must be one of {SIDES}")
            users.append(User(name=name, email=raw["email"], password=_password(name, raw), sides=sides))
    except KeyError as e:
        raise ConfigError(f"missing required config key: {e}") from None

    if not users:
        raise ConfigError("add at least one user")

    when = data.get("when_away_from_all", "away")
    if when not in ("away", "unchanged"):
        raise ConfigError("when_away_from_all must be 'away' or 'unchanged'")

    max_acc = data.get("max_accuracy_miles")
    return Config(
        away_miles=float(data.get("away_miles", 50)),
        switch_margin_miles=float(data.get("switch_margin_miles", 10)),
        hysteresis_miles=float(data.get("hysteresis_miles", 5)),
        when_away_from_all=when,
        users=users,
        beds=beds,
        state_file=os.environ.get("LOCEIGHT_STATE_FILE", data.get("state_file", "state.json")),
        redis_url=os.environ.get("REDIS_URL") or data.get("redis_url"),
        max_accuracy_miles=float(max_acc) if max_acc is not None else None,
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
