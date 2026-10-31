"""Minimal client for Eight Sleep's (unofficial) cloud API.

Eight Sleep has no public API. These endpoints are the ones the mobile app uses,
as documented by the open-source pyEight library behind the Home Assistant
integration (https://github.com/lukas-clarke/pyEight). They can change without
notice.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

AUTH_URL = "https://auth-api.8slp.net/v1/tokens"
CLIENT_API_URL = "https://client-api.8slp.net/v1"
APP_API_URL = "https://app-api.8slp.net/v1"

# Public client credentials the Eight Sleep app uses (they are the same for everyone).
CLIENT_ID = "0894c7f33bb94800a03f1f4df13a4f38"
CLIENT_SECRET = "f0954a3ed5763ba3d06834c73731a32f15f168f47d4f164751275def86db0c76"

HEADERS = {
    "content-type": "application/json",
    "accept": "application/json",
    "user-agent": "locEIGHT/1.0",
}
TIMEOUT = 20


class EightSleepError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class EightSleep:
    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self._token: str | None = None
        self._expires = 0.0
        self.user_id: str | None = None

    # -- low level -----------------------------------------------------------

    def _http(self, method: str, url: str, body: dict | None = None, auth: bool = True):
        headers = dict(HEADERS)
        if auth:
            headers["authorization"] = f"Bearer {self._ensure_token()}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 401 and auth:
                self._token = None  # force a fresh login on the next call
            detail = e.read().decode(errors="replace")[:300]
            raise EightSleepError(f"{method} {url} -> HTTP {e.code}: {detail}", e.code) from None
        except urllib.error.URLError as e:
            raise EightSleepError(f"{method} {url} failed: {e.reason}") from None
        return json.loads(raw) if raw.strip() else None

    def _ensure_token(self) -> str:
        if self._token and time.time() < self._expires - 120:
            return self._token
        resp = self._http(
            "POST",
            AUTH_URL,
            {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "grant_type": "password",
                "username": self.email,
                "password": self.password,
            },
            auth=False,
        )
        self._token = resp["access_token"]
        self._expires = time.time() + float(resp.get("expires_in", 3600))
        self.user_id = resp["userId"]
        return self._token

    def _uid(self) -> str:
        self._ensure_token()
        assert self.user_id
        return self.user_id

    # -- operations ----------------------------------------------------------

    def me(self) -> dict:
        """The account profile, including its `devices` and `currentDevice`."""
        return self._http("GET", f"{CLIENT_API_URL}/users/me")["user"]

    def device(self, device_id: str) -> dict:
        """Details for one Pod, including `leftUserId` / `rightUserId` / `awaySides`."""
        resp = self._http("GET", f"{CLIENT_API_URL}/devices/{device_id}")
        return resp.get("result", resp)

    def household_summary(self) -> dict:
        """Every Pod in the household, grouped into sets (this is where a second
        home or guest room Pod shows up; `users/me` only lists the active one)."""
        return self._http("GET", f"{APP_API_URL}/household/users/{self._uid()}/summary")

    def household_current_set(self) -> dict:
        return self._http("GET", f"{APP_API_URL}/household/users/{self._uid()}/current-set")

    def set_current_set(self, set_id: str, user_id: str | None = None) -> None:
        """Make a household "set" (one home's Pod) the active one for a person.

        This is how the app switches between Pods on a multi-Pod account."""
        uid = user_id or self._uid()
        self._http("PUT", f"{APP_API_URL}/household/users/{uid}/current-set", {"setId": set_id})

    def set_ids_by_device(self) -> dict[str, str]:
        out = {}
        for household in self.household_summary().get("households", []):
            for pod_set in household.get("sets", []):
                for dev in pod_set.get("devices", []):
                    if isinstance(dev, dict) and dev.get("deviceId"):
                        out[dev["deviceId"]] = pod_set.get("setId")
        return out

    def household_device_ids(self) -> list[str]:
        ids = []
        for household in self.household_summary().get("households", []):
            for pod_set in household.get("sets", []):
                for dev in pod_set.get("devices", []):
                    dev_id = dev.get("deviceId") or dev.get("id") if isinstance(dev, dict) else dev
                    if dev_id and dev_id not in ids:
                        ids.append(dev_id)
        return ids

    def user_name(self, user_id: str) -> str:
        """First name of a person on the account (best effort, for messages)."""
        try:
            user = self._http("GET", f"{CLIENT_API_URL}/users/{user_id}")["user"]
            return user.get("firstName") or user_id[:8]
        except Exception:
            return user_id[:8]

    def set_away(self, away: bool, user_id: str | None = None) -> None:
        """Turn away mode on or off for one person (default: the logged-in user).

        One login can control both sides of a Pod, the same way the Home
        Assistant integration does. The timestamp is set a day in the past so the
        change applies immediately.
        """
        when = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        action = "start" if away else "end"
        uid = user_id or self._uid()
        self._http("PUT", f"{APP_API_URL}/users/{uid}/away-mode", {"awayPeriod": {action: when}})

    def set_current_device(self, device_id: str, side: str, user_id: str | None = None) -> None:
        """Make a Pod (and side) the one a person is currently sleeping on."""
        uid = user_id or self._uid()
        self._http("PUT", f"{CLIENT_API_URL}/users/{uid}/current-device", {"id": device_id, "side": side})
