"""Decide which bed is "home" for a location and push that to Eight Sleep.

Changes are made only when you move from one area to another (near bed A, near
bed B, or near neither). Repeated location updates in the same area do nothing,
so a manual change in the Eight Sleep app is never undone until you next travel.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .config import Bed, Config, User
from .eightsleep import EightSleep
from .geo import distance_miles
from .state import make_store

log = logging.getLogger("loceight")


@dataclass
class Decision:
    home: str | None
    distances: dict[str, float]


@dataclass
class Result:
    home: str | None
    distances: dict[str, float]
    changed: bool = False
    actions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: str | None = None

    def to_dict(self) -> dict:
        return {
            "home": self.home,
            "changed": self.changed,
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


ClientFactory = Callable[[User], EightSleep]


class Controller:
    def __init__(self, cfg: Config, client_factory: ClientFactory | None = None, store=None):
        self.cfg = cfg
        self.store = store or make_store(cfg.redis_url, cfg.state_file)
        self._factory = client_factory or (lambda u: EightSleep(u.email, u.password))
        self._clients: dict[str, EightSleep] = {}
        self._lock = threading.Lock()

    def client(self, user: User) -> EightSleep:
        if user.name not in self._clients:
            self._clients[user.name] = self._factory(user)
        return self._clients[user.name]

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
        previous = state.get("home")
        decision = decide(self.cfg, lat, lon, previous)
        result = Result(home=decision.home, distances=decision.distances)

        limit = self.cfg.max_accuracy_miles
        if limit is not None and accuracy_miles is not None and accuracy_miles > limit:
            result.skipped = f"location accuracy {accuracy_miles:.1f} mi is worse than {limit} mi"
            return result

        first_run = "home" not in state
        result.changed = first_run or decision.home != previous
        if result.changed or force:
            self._switch(decision.home, result, dry_run)

        if not dry_run:
            # Only remember the new area once every user was switched, so a failed
            # call is retried on the next update.
            if not result.errors:
                state["home"] = decision.home
                state["switched_at"] = int(time.time()) if (result.changed or force) else state.get("switched_at")
                state.pop("last_errors", None)
            else:
                state["last_errors"] = result.errors
            state["last_location"] = {"lat": lat, "lon": lon, "at": int(time.time())}
            state["distances_miles"] = result.to_dict()["distances_miles"]
            self.store.save(state)
        return result

    def _bed(self, name: str) -> Bed:
        return next(b for b in self.cfg.beds if b.name == name)

    def _switch(self, home: str | None, result: Result, dry_run: bool) -> None:
        if home is None and self.cfg.when_away_from_all == "unchanged":
            return
        bed = self._bed(home) if home else None
        for user in self.cfg.users:
            try:
                if bed is None:
                    result.actions.append(f"{user.name}: away")
                    if not dry_run:
                        self.client(user).set_away(True)
                    continue
                if not bed.device_id:
                    raise ValueError(f"bed '{bed.name}' has no device_id in the config yet")
                result.actions.append(f"{user.name}: home at {bed.name}")
                if not dry_run:
                    c = self.client(user)
                    side = user.sides.get(bed.name) or c.side_on(bed.device_id)
                    c.set_current_device(bed.device_id, side)
                    c.set_away(False)
            except Exception as e:  # keep going with the other user; retried next update
                log.exception("switching %s failed", user.name)
                result.errors.append(f"{user.name}: {e}")
