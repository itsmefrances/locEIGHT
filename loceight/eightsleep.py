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
    pass


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
            raise EightSleepError(f"{method} {url} -> HTTP {e.code}: {detail}") from None
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
        """Details for one Pod, including `leftUserId` / `rightUserId`."""
        resp = self._http("GET", f"{CLIENT_API_URL}/devices/{device_id}")
        return resp.get("result", resp)

    def side_on(self, device_id: str) -> str:
        """Which side of the given Pod this account sleeps on."""
        info = self.device(device_id)
        uid = self._uid()
        left, right = info.get("leftUserId") == uid, info.get("rightUserId") == uid
        if left and right:
            return "solo"
        if left:
            return "left"
        if right:
            return "right"
        raise EightSleepError(f"{self.email} is not assigned to a side of Pod {device_id}")

    def set_away(self, away: bool) -> None:
        """Turn the account's away mode on or off.

        The timestamp is set a day in the past so the change applies immediately
        (same trick pyEight uses).
        """
        when = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        action = "start" if away else "end"
        self._http("PUT", f"{APP_API_URL}/users/{self._uid()}/away-mode", {"awayPeriod": {action: when}})

    def set_current_device(self, device_id: str, side: str = "solo") -> None:
        """Point the account at a specific Pod (for one account with several Pods)."""
        self._http(
            "PUT",
            f"{CLIENT_API_URL}/users/{self._uid()}/current-device",
            {"id": device_id, "side": side},
        )
