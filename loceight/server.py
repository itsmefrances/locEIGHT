"""Tiny HTTP server that receives location updates from your phone.

  POST /location   {"lat": 40.7, "lon": -74.0, "accuracy_m": 30}
                   OwnTracks HTTP payloads ({"_type": "location", ...}) also work.
  GET  /status     last known state
  GET  /healthz    liveness check (no auth)

Every endpoint except /healthz needs the shared token, given as
`Authorization: Bearer <token>`, HTTP basic auth (token as password), or `?token=`.
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .controller import Controller

log = logging.getLogger("loceight")

METERS_PER_MILE = 1609.344


def parse_location(payload: dict) -> tuple[float, float, float | None] | None:
    """Return (lat, lon, accuracy_miles), or None if this isn't a location update."""
    if payload.get("_type", "location") != "location":
        return None  # OwnTracks sends other message types too; ignore them
    lat = payload.get("lat", payload.get("latitude"))
    lon = payload.get("lon", payload.get("longitude"))
    if lat is None or lon is None:
        raise ValueError("payload needs 'lat' and 'lon'")
    acc_m = payload.get("accuracy_m", payload.get("acc"))
    acc = float(acc_m) / METERS_PER_MILE if acc_m not in (None, "") else None
    lat, lon = float(lat), float(lon)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("lat/lon out of range")
    return lat, lon, acc


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

        def _authorized(self, query: dict) -> bool:
            supplied = None
            header = self.headers.get("authorization", "")
            if header.lower().startswith("bearer "):
                supplied = header[7:].strip()
            elif header.lower().startswith("basic "):
                try:
                    supplied = base64.b64decode(header[6:]).decode().split(":", 1)[-1]
                except Exception:
                    supplied = None
            elif "token" in query:
                supplied = query["token"][0]
            return supplied is not None and hmac.compare_digest(supplied.encode(), token.encode())

        def do_GET(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path == "/healthz":
                return self._send(200, {"ok": True})
            if not self._authorized(query):
                return self._send(401, {"error": "unauthorized"})
            if url.path == "/status":
                return self._send(200, controller.status())
            if url.path == "/location":  # GET /location?lat=..&lon=.. for quick testing
                return self._handle_location({k: v[0] for k, v in query.items()}, query)
            self._send(404, {"error": "not found"})

        def do_POST(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if not self._authorized(query):
                return self._send(401, {"error": "unauthorized"})
            if url.path != "/location":
                return self._send(404, {"error": "not found"})
            try:
                length = int(self.headers.get("content-length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("expected a JSON object")
            except ValueError as e:
                return self._send(400, {"error": f"bad JSON: {e}"})
            self._handle_location(payload, query)

        def _handle_location(self, payload: dict, query: dict):
            owntracks = "_type" in payload
            try:
                loc = parse_location(payload)
            except (TypeError, ValueError) as e:
                return self._send(400, {"error": str(e)})
            if loc is None:
                return self._send(200, [])
            lat, lon, acc = loc
            dry = query.get("dry_run", ["0"])[0] in ("1", "true")
            result = controller.update(lat, lon, accuracy_miles=acc, dry_run=dry)
            log.info("location -> %s", result.to_dict())
            # OwnTracks expects a JSON array back.
            self._send(200, [] if owntracks else result.to_dict())

    return Handler


def serve(controller: Controller, token: str, host: str = "0.0.0.0", port: int = 8080) -> None:
    httpd = ThreadingHTTPServer((host, port), make_handler(controller, token))
    log.info("locEIGHT listening on %s:%d", host, port)
    httpd.serve_forever()
