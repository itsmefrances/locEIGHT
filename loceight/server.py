"""Tiny HTTP server that receives location updates from the iPhone shortcut.

  GET /location?token=..&lat=..&lon=..   apply a location (add &dry_run=1 to preview)
  GET /status?token=..                   last known state
  GET /healthz                           liveness check (no token needed)
"""

from __future__ import annotations

import hmac
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .controller import Controller

log = logging.getLogger("loceight")


def parse_location(query: dict) -> tuple[float, float]:
    """Read lat/lon from the query string."""
    try:
        lat, lon = float(query["lat"][0]), float(query["lon"][0])
    except (KeyError, IndexError, ValueError):
        raise ValueError("need numeric 'lat' and 'lon'") from None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("lat/lon out of range")
    return lat, lon


def make_handler(controller: Controller, token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "locEIGHT"

        def log_message(self, fmt, *args):
            pass  # default access log would record the token and exact coordinates

        def log_request(self, code="-", size="-"):
            path = urlparse(self.path).path  # drop the query string (token, lat/lon)
            log.info("%s %s -> %s", self.command, path, code)

        def _send(self, code: int, body) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path == "/healthz":
                return self._send(200, {"ok": True})
            supplied = query.get("token", [""])[0]
            if not hmac.compare_digest(supplied.encode(), token.encode()):
                return self._send(401, {"error": "unauthorized"})
            if url.path == "/status":
                return self._send(200, controller.status())
            if url.path == "/location":
                try:
                    lat, lon = parse_location(query)
                except ValueError as e:
                    return self._send(400, {"error": str(e)})
                dry = query.get("dry_run", ["0"])[0] in ("1", "true")
                result = controller.update(lat, lon, dry_run=dry)
                log.info("location -> %s", result.to_dict())
                return self._send(200, result.to_dict())
            self._send(404, {"error": "not found"})

    return Handler


def serve(controller: Controller, token: str, host: str = "0.0.0.0", port: int = 8080) -> None:
    httpd = ThreadingHTTPServer((host, port), make_handler(controller, token))
    log.info("locEIGHT listening on %s:%d", host, port)
    httpd.serve_forever()
