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

from .config import Bed, Config
from .eightsleep import EightSleep, EightSleepError
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
    """Pick the bed you're nearest to, or None if you're far from all of them.

    - The nearest bed is home, as long as it's within `away_miles`. This means
      the drive between two homes never counts as "away": partway there, the
      bed you're heading to simply takes over.
    - To avoid flip-flopping around the halfway point, an already-home bed keeps
      the role until another bed is `switch_margin_miles` closer.
    - Once home, you only count as away beyond `away_miles + hysteresis_miles`.
    """
    distances = {b.name: distance_miles(lat, lon, b.lat, b.lon) for b in cfg.beds}
    nearest = min(distances, key=distances.__getitem__)
    was_home = previous_home in distances

    away_limit = cfg.away_miles + (cfg.hysteresis_miles if was_home else 0)
    if distances[nearest] > away_limit:
        return Decision(home=None, distances=distances)
    if was_home and nearest != previous_home:
        if distances[previous_home] - distances[nearest] < cfg.switch_margin_miles:
            return Decision(home=previous_home, distances=distances)
    return Decision(home=nearest, distances=distances)


def side_assignments(info: dict) -> dict[str, str]:
    """{user_id: side} for a Pod.

    While someone is in away mode, Eight Sleep may blank or collapse the
    top-level left/right IDs; `awaySides` keeps the real assignment (same logic
    as the open-source eightctl tool).
    """
    left, right = info.get("leftUserId") or "", info.get("rightUserId") or ""
    away = info.get("awaySides") or {}
    away_left, away_right = away.get("leftUserId") or "", away.get("rightUserId") or ""
    if away_left and away_right and away_left != away_right:
        left, right = away_left, away_right
    else:
        left, right = left or away_left, right or away_right
    if left and right:
        return {left: "solo"} if left == right else {left: "left", right: "right"}
    if left or right:
        return {left or right: "solo"}
    return {}


ClientFactory = Callable[[Config], EightSleep]


class Controller:
    def __init__(self, cfg: Config, client_factory: ClientFactory | None = None, store=None):
        self.cfg = cfg
        self.store = store or make_store(cfg.redis_url, cfg.state_file)
        self._factory = client_factory or (lambda c: EightSleep(c.account.email, c.account.password))
        self._client: EightSleep | None = None
        self._sleepers: dict[str, dict[str, str]] | None = None
        self._names: dict[str, str] = {}
        self._set_ids: dict[str, str] | None = None
        self._lock = threading.Lock()

    @property
    def client(self) -> EightSleep:
        if self._client is None:
            self._client = self._factory(self.cfg)
        return self._client

    def sleepers(self) -> dict[str, dict[str, str]]:
        """Who sleeps on which managed bed: {user_id: {bed_name: side}}.

        Read from each Pod's left/right assignments, so both of you (and only the
        people on the managed beds) are switched together.
        """
        if self._sleepers is None:
            found: dict[str, dict[str, str]] = {}
            for bed in self.cfg.beds:
                if not bed.device_id:
                    raise ValueError(f"bed '{bed.name}' has no device_id in the config yet")
                info = self.client.device(bed.device_id)
                for uid, side in side_assignments(info).items():
                    found.setdefault(uid, {})[bed.name] = side
            if not found:
                raise ValueError("no one is assigned to the managed beds")
            self._sleepers = found
        return self._sleepers

    def make_active(self, bed: Bed, side: str, uid: str) -> str:
        """Point a person at a bed. Prefer the household "current set" (what the
        app uses on multi-Pod accounts); fall back to the older current-device
        call if the account has no household or the call is refused."""
        if self._set_ids is None:
            try:
                self._set_ids = self.client.set_ids_by_device()
            except EightSleepError as e:
                log.warning("household lookup failed (%s); using current-device", e)
                self._set_ids = {}
        set_id = self._set_ids.get(bed.device_id)
        if set_id:
            try:
                self.client.set_current_set(set_id, uid)
                return "household"
            except EightSleepError as e:
                if e.status not in (400, 404, 405):
                    raise
                log.warning("current-set refused (%s); falling back to current-device", e)
        self.client.set_current_device(bed.device_id, side, uid)
        return "device"

    def name(self, user_id: str) -> str:
        if user_id not in self._names:
            self._names[user_id] = self.client.user_name(user_id)
        return self._names[user_id]

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
        try:
            sleepers = self.sleepers()
        except Exception as e:
            log.exception("could not read who sleeps on the beds")
            result.errors.append(str(e))
            return
        bed = self._bed(home) if home else None
        for uid, sides in sleepers.items():
            who = self.name(uid)
            try:
                if bed is None or bed.name not in sides:
                    # Far from every bed, or this person has no side on the home bed.
                    result.actions.append(f"{who}: away")
                    if not dry_run:
                        self.client.set_away(True, uid)
                    continue
                result.actions.append(f"{who}: home at {bed.name} ({sides[bed.name]} side)")
                if not dry_run:
                    how = self.make_active(bed, sides[bed.name], uid)
                    self.client.set_away(False, uid)
                    log.info("%s now active on %s via %s", who, bed.name, how)
            except Exception as e:  # keep going with the other person; retried next update
                log.exception("switching %s failed", who)
                result.errors.append(f"{who}: {e}")
