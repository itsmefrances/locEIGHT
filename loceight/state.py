"""Where locEIGHT remembers which bed it last switched to.

Hosting platforms often wipe local files on restart, which would make the
service forget and re-apply a change (undoing any manual override). Set
REDIS_URL (e.g. a free Render Key Value instance) to keep the memory safe;
otherwise a local JSON file is used.
"""

from __future__ import annotations

import json
import os
import socket
import ssl
from urllib.parse import unquote, urlparse

KEY = "loceight:state"


class FileStore:
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


class RedisStore:
    """Just enough of the Redis protocol for GET and SET (standard library only)."""

    def __init__(self, url: str):
        u = urlparse(url)
        self.host, self.port = u.hostname or "localhost", u.port or 6379
        self.tls = u.scheme == "rediss"
        self.username = unquote(u.username) if u.username else None
        self.password = unquote(u.password) if u.password else None
        self.db = int(u.path.lstrip("/") or 0)

    def _connect(self):
        sock = socket.create_connection((self.host, self.port), timeout=10)
        if self.tls:
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)
        return sock

    @staticmethod
    def _encode(*args: str) -> bytes:
        out = [f"*{len(args)}\r\n".encode()]
        for a in args:
            b = a.encode()
            out.append(b"$%d\r\n%s\r\n" % (len(b), b))
        return b"".join(out)

    @staticmethod
    def _reply(f):
        line = f.readline().rstrip(b"\r\n")
        kind, rest = line[:1], line[1:]
        if kind == b"-":
            raise RuntimeError(f"redis error: {rest.decode()}")
        if kind == b"$":
            n = int(rest)
            return None if n < 0 else f.read(n + 2)[:-2].decode()
        return rest.decode()

    def _run(self, *cmd: str):
        with self._connect() as sock, sock.makefile("rb") as f:
            prelude = []
            if self.password:
                prelude.append(("AUTH", self.username, self.password) if self.username else ("AUTH", self.password))
            if self.db:
                prelude.append(("SELECT", str(self.db)))
            for c in prelude + [cmd]:
                sock.sendall(self._encode(*c))
                result = self._reply(f)
            return result

    def load(self) -> dict:
        raw = self._run("GET", KEY)
        return json.loads(raw) if raw else {}

    def save(self, state: dict) -> None:
        self._run("SET", KEY, json.dumps(state))


def make_store(redis_url: str | None, path: str):
    return RedisStore(redis_url) if redis_url else FileStore(path)
