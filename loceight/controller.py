"""Decide which bed is "home" for a location and push that to Eight Sleep."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .config import Account, Bed, Config
from .eightsleep import EightSleep
from .geo import distance_miles

log = logging.getLogger("loceight")


@dataclass
class Decision:
    home: str | None
    distances: dict[str, float]


@dataclass
class Result:
    home: str | None
    distances: dict[str, float]
    actions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: str | None = None

    def to_dict(self) -> dict:
        return {
            "home": self.home,
            "distances_miles": {k: round(v, 1) for k, v in self.distances.items()},
            "actions": self.actions,
            "errors": self.errors,
            "skipped": self.skipped,
        }


def decide(cfg: Config, lat: float, lon: float, previous_home: str | None = None) -> Decision:
    """Pick the bed you're near, if any.

    A bed counts as "near" inside `radius_miles`. Once a bed is home it stays home
    until you're more than `radius_miles + hysteresis_miles` away, so hovering at
    the edge of the radius doesn't flip the beds back and forth.
    """
    distances = {b.name: distance_miles(lat, lon, b.lat, b.lon) for b in cfg.beds}
    candidates = []
    for bed in cfg.beds:
        limit = cfg.radius_miles
        if bed.name == previous_home:
            limit += cfg.hysteresis_miles
        if distances[bed.name] <= limit:
            candidates.append(bed.name)
    home = min(candidates, key=distances.__getitem__) if candidates else None
    return Decision(home=home, distances=distances)


class StateStore:
    def __init__(self, path: str):
        self.path = path

    def load(self) -> dict:
        try:
            with open(self.path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save(self, state: dict) -> None:
        tmp = f"{self.path}.tmp"
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, self.path)


ClientFactory = Callable[[Account], EightSleep]


class Controller:
    def __init__(self, cfg: Config, client_factory: ClientFactory | None = None):
        self.cfg = cfg
        self.store = StateStore(cfg.state_file)
        self._factory = client_factory or (lambda a: EightSleep(a.email, a.password))
        self._clients: dict[str, EightSleep] = {}
        self._lock = threading.Lock()

    def client(self, account_key: str) -> EightSleep:
        if account_key not in self._clients:
            self._clients[account_key] = self._factory(self.cfg.accounts[account_key])
        return self._clients[account_key]

    def status(self) -> dict:
        return self.store.load()

    def update(
        self,
        lat: float,
        lon: float,
        accuracy_miles: float | None = None,
        dry_run: bool = False,
        force: bool = False,
    ) -> Result:
        with self._lock:
            return self._update(lat, lon, accuracy_miles, dry_run, force)

    def _update(self, lat, lon, accuracy_miles, dry_run, force) -> Result:
        state = self.store.load()
        decision = decide(self.cfg, lat, lon, state.get("home"))
        result = Result(home=decision.home, distances=decision.distances)

        limit = self.cfg.max_accuracy_miles
        if limit is not None and accuracy_miles is not None and accuracy_miles > limit:
            result.skipped = f"location accuracy {accuracy_miles:.1f} mi is worse than {limit} mi"
            return result

        accounts_state = state.setdefault("accounts", {})
        for key, beds in self.cfg.beds_by_account.items():
            self._apply_account(key, beds, decision.home, accounts_state, result, dry_run, force)

        if not dry_run:
            state["home"] = decision.home
            state["last_location"] = {"lat": lat, "lon": lon, "at": int(time.time())}
            state["distances_miles"] = result.to_dict()["distances_miles"]
            if result.errors:
                state["last_errors"] = result.errors
            else:
                state.pop("last_errors", None)
            self.store.save(state)
        return result

    def _apply_account(self, key, beds: list[Bed], home, accounts_state, result, dry_run, force):
        acct = accounts_state.setdefault(key, {})
        home_bed = next((b for b in beds if b.name == home), None)
        names = "/".join(b.name for b in beds)

        if home_bed is not None:
            want_away = False
        elif home is None and self.cfg.when_away_from_all == "unchanged":
            return
        else:
            want_away = True

        try:
            if home_bed is not None and len(beds) > 1:
                if force or acct.get("current_device") != home_bed.device_id:
                    result.actions.append(f"{key}: switch active Pod to {home_bed.name}")
                    if not dry_run:
                        self.client(key).set_current_device(home_bed.device_id, home_bed.side)
                        acct["current_device"] = home_bed.device_id
            if force or acct.get("away") != want_away:
                label = "away" if want_away else "home"
                target = home_bed.name if home_bed else names
                result.actions.append(f"{key}: set {target} to {label}")
                if not dry_run:
                    self.client(key).set_away(want_away)
                    acct["away"] = want_away
        except Exception as e:  # keep going with other accounts; retry next update
            log.exception("account %s failed", key)
            result.errors.append(f"{key}: {e}")
