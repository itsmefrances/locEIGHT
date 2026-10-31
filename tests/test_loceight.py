import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from loceight import config
from loceight.controller import Controller, decide
from loceight.geo import distance_miles
from loceight.server import make_handler, parse_location
from loceight.state import RedisStore

NYC = (40.7836, -73.9805)  # 2211 Broadway
HAMPTONS = (41.7911, -74.2288)  # Accord, NY (~71 mi from NYC); name kept for brevity
NEWARK = (40.7357, -74.1724)  # ~11 mi from NYC
NEWBURGH = (41.5034, -74.0104)  # on the drive: 50 mi from NYC, 23 from Accord
MIDWAY = (41.2873, -74.1047)  # about halfway between the homes
ALBANY = (42.6526, -73.7562)  # 64 mi from Accord -> away
LA = (34.0522, -118.2437)


DEVICES = {
    "dev-city": {"leftUserId": "u-me", "rightUserId": "u-dani"},
    "dev-beach": {"leftUserId": "u-dani", "rightUserId": "u-me"},
}


def make_cfg(**overrides):
    data = {
        "away_miles": 50,
        "switch_margin_miles": 10,
        "account": {"email": "me@example.com", "password": "x"},
        "beds": [
            {"name": "City", "lat": NYC[0], "lon": NYC[1], "device_id": "dev-city"},
            {"name": "Beach", "lat": HAMPTONS[0], "lon": HAMPTONS[1], "device_id": "dev-beach"},
        ],
    }
    data.update(overrides)
    cfg = config.parse(data)
    cfg.redis_url = None
    cfg.state_file = os.path.join(tempfile.mkdtemp(), "state.json")
    return cfg


class FakeClient:
    def __init__(self, log, failing=(), devices=DEVICES):
        self.log, self.failing, self.devices = log, failing, devices

    def device(self, device_id):
        return self.devices[device_id]

    def user_name(self, uid):
        return {"u-me": "Me", "u-dani": "Dani"}.get(uid, uid)

    def set_away(self, away, user_id):
        if user_id in self.failing:
            raise RuntimeError("boom")
        self.log.append((self.user_name(user_id), "away" if away else "home"))

    def set_current_device(self, device_id, side, user_id):
        if user_id in self.failing:
            raise RuntimeError("boom")
        self.log.append((self.user_name(user_id), device_id, side))


def controller(cfg, failing=(), devices=DEVICES):
    calls = []
    ctl = Controller(cfg, lambda c: FakeClient(calls, failing, devices))
    return ctl, calls


HOME_CITY = [("Me", "dev-city", "left"), ("Me", "home"), ("Dani", "dev-city", "right"), ("Dani", "home")]
HOME_BEACH = [("Me", "dev-beach", "right"), ("Me", "home"), ("Dani", "dev-beach", "left"), ("Dani", "home")]
ALL_AWAY = [("Me", "away"), ("Dani", "away")]


class GeoTest(unittest.TestCase):
    def test_distance(self):
        self.assertAlmostEqual(distance_miles(*NYC, *LA), 2445, delta=10)
        self.assertEqual(distance_miles(*NYC, *NYC), 0)


class DecideTest(unittest.TestCase):
    def test_near_one_bed(self):
        cfg = make_cfg()
        self.assertEqual(decide(cfg, *NEWARK).home, "City")
        self.assertEqual(decide(cfg, *HAMPTONS).home, "Beach")
        self.assertIsNone(decide(cfg, *LA).home)

    def test_drive_between_homes_never_goes_away(self):
        cfg = make_cfg()
        self.assertEqual(decide(cfg, *NEWBURGH).home, "Beach")
        self.assertIn(decide(cfg, *MIDWAY).home, ("City", "Beach"))
        self.assertEqual(decide(cfg, *ALBANY).home, None)

    def test_switch_margin_prevents_flip_flop_at_halfway(self):
        cfg = make_cfg()
        d = decide(cfg, *MIDWAY).distances
        self.assertLess(abs(d["City"] - d["Beach"]), 10)
        self.assertEqual(decide(cfg, *MIDWAY, previous_home="City").home, "City")
        self.assertEqual(decide(cfg, *MIDWAY, previous_home="Beach").home, "Beach")

    def test_away_hysteresis(self):
        cfg = make_cfg()
        point = (NYC[0], NYC[1] - 52 / 52.4)  # ~52 mi west of NYC, ~60 from Accord
        self.assertIsNone(decide(cfg, *point).home)
        self.assertEqual(decide(cfg, *point, previous_home="City").home, "City")


class ControllerTest(unittest.TestCase):
    def test_both_users_move_together_once_per_trip(self):
        ctl, calls = controller(make_cfg())
        r = ctl.update(*NEWARK)
        self.assertEqual(r.home, "City")
        self.assertEqual(calls, HOME_CITY)

        calls.clear()
        ctl.update(*NYC)  # still near City -> nothing sent
        ctl.update(*NEWARK)
        self.assertEqual(calls, [])

        ctl.update(*HAMPTONS)
        self.assertEqual(calls, HOME_BEACH)

        calls.clear()
        ctl.update(*LA)
        self.assertEqual(calls, ALL_AWAY)
        calls.clear()
        ctl.update(*LA)  # still far away -> nothing sent
        self.assertEqual(calls, [])

    def test_manual_override_is_not_undone(self):
        # Pretend the user fixed something by hand in the app; with no trip,
        # repeated updates send nothing, so their change sticks.
        ctl, calls = controller(make_cfg())
        ctl.update(*NYC)
        calls.clear()
        for _ in range(5):
            ctl.update(*NYC)
        self.assertEqual(calls, [])

    def test_unchanged_when_far_from_all(self):
        ctl, calls = controller(make_cfg(when_away_from_all="unchanged"))
        ctl.update(*NYC)
        calls.clear()
        ctl.update(*LA)
        self.assertEqual(calls, [])
        ctl.update(*NYC)  # coming back still counts as a trip
        self.assertEqual(calls, HOME_CITY)

    def test_failure_is_retried(self):
        cfg = make_cfg()
        ctl, calls = controller(cfg, failing={"u-dani"})
        r = ctl.update(*NYC)
        self.assertEqual(len(r.errors), 1)
        self.assertEqual(ctl.status().get("home", "unset"), "unset")
        ctl2, calls2 = controller(cfg)
        ctl2.update(*NYC)
        self.assertEqual(calls2, HOME_CITY)
        calls2.clear()
        ctl2.update(*NYC)
        self.assertEqual(calls2, [])

    def test_missing_device_id_reports_error(self):
        cfg = make_cfg(beds=[{"name": "City", "lat": NYC[0], "lon": NYC[1]}])
        ctl, calls = controller(cfg)
        r = ctl.update(*NYC)
        self.assertEqual(len(r.errors), 1)
        self.assertIn("device_id", r.errors[0])
        self.assertEqual(calls, [])

    def test_force_resends(self):
        ctl, calls = controller(make_cfg())
        ctl.update(*NYC)
        calls.clear()
        ctl.update(*NYC, force=True)
        self.assertEqual(calls, HOME_CITY)

    def test_dry_run_changes_nothing(self):
        ctl, calls = controller(make_cfg())
        r = ctl.update(*NYC, dry_run=True)
        self.assertEqual(calls, [])
        self.assertEqual(r.actions, ["Me: home at City (left side)", "Dani: home at City (right side)"])
        self.assertEqual(ctl.status(), {})

    def test_poor_accuracy_skipped(self):
        ctl, calls = controller(make_cfg(max_accuracy_miles=2))
        r = ctl.update(*NYC, accuracy_miles=10)
        self.assertIsNotNone(r.skipped)
        self.assertEqual(calls, [])

    def test_password_env(self):
        os.environ["TEST_PW"] = "secret"
        cfg = make_cfg(account={"email": "e", "password_env": "TEST_PW"})
        self.assertEqual(cfg.account.password, "secret")

    def test_solo_sleeper_and_person_missing_from_home_bed(self):
        devices = {
            "dev-city": {"leftUserId": "u-me", "rightUserId": "u-me"},  # Me alone, whole bed
            "dev-beach": {"leftUserId": "u-dani", "rightUserId": "u-me"},
        }
        ctl, calls = controller(make_cfg(), devices=devices)
        ctl.update(*NYC)
        # Dani has no side on the City bed, so she is set away rather than left on Beach.
        self.assertEqual(calls, [("Me", "dev-city", "solo"), ("Me", "home"), ("Dani", "away")])


class RedisStoreTest(unittest.TestCase):
    def test_round_trip_against_fake_server(self):
        import socketserver

        store_data = {}

        class H(socketserver.StreamRequestHandler):
            def handle(self):
                while True:
                    line = self.rfile.readline()
                    if not line:
                        return
                    n = int(line[1:])
                    args = []
                    for _ in range(n):
                        size = int(self.rfile.readline()[1:])
                        args.append(self.rfile.read(size + 2)[:-2].decode())
                    cmd = args[0].upper()
                    if cmd == "AUTH":
                        ok = args[-1] == "pw"
                        self.wfile.write(b"+OK\r\n" if ok else b"-WRONGPASS\r\n")
                    elif cmd == "SET":
                        store_data[args[1]] = args[2]
                        self.wfile.write(b"+OK\r\n")
                    elif cmd == "GET":
                        v = store_data.get(args[1])
                        if v is None:
                            self.wfile.write(b"$-1\r\n")
                        else:
                            b = v.encode()
                            self.wfile.write(b"$%d\r\n%s\r\n" % (len(b), b))

        srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            store = RedisStore(f"redis://:pw@127.0.0.1:{srv.server_address[1]}")
            self.assertEqual(store.load(), {})
            store.save({"home": "City"})
            self.assertEqual(store.load(), {"home": "City"})
            with self.assertRaises(RuntimeError):
                RedisStore(f"redis://:bad@127.0.0.1:{srv.server_address[1]}").load()
        finally:
            srv.shutdown()
            srv.server_close()


class ServerTest(unittest.TestCase):
    TOKEN = "t" * 20

    def setUp(self):
        self.ctl, self.calls = controller(make_cfg())
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.ctl, self.TOKEN))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def post(self, body, token=TOKEN, path="/location"):
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode(),
            method="POST",
            headers={"authorization": f"Bearer {token}", "content-type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    def test_location(self):
        out = self.post({"lat": NYC[0], "lon": NYC[1]})
        self.assertEqual(out["home"], "City")
        self.assertEqual(self.calls, HOME_CITY)

    def test_owntracks(self):
        self.assertEqual(self.post({"_type": "location", "lat": HAMPTONS[0], "lon": HAMPTONS[1], "acc": 20}), [])
        self.assertEqual(self.post({"_type": "transition"}), [])
        self.assertEqual(self.calls, HOME_BEACH)

    def test_rejects_bad_token(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post({"lat": 0, "lon": 0}, token="wrong")
        self.assertEqual(cm.exception.code, 401)

    def test_query_token_and_healthz(self):
        with urllib.request.urlopen(f"{self.base}/healthz") as r:
            self.assertEqual(json.loads(r.read()), {"ok": True})
        url = f"{self.base}/location?lat={NYC[0]}&lon={NYC[1]}&token={self.TOKEN}&dry_run=1"
        with urllib.request.urlopen(url) as r:
            self.assertEqual(json.loads(r.read())["home"], "City")
        self.assertEqual(self.calls, [])


class ParseTest(unittest.TestCase):
    def test_parse(self):
        lat, lon, acc = parse_location({"latitude": "1.5", "longitude": "2", "accuracy_m": 1609.344})
        self.assertEqual((lat, lon, acc), (1.5, 2.0, 1.0))
        with self.assertRaises(ValueError):
            parse_location({"lat": 100, "lon": 0})


if __name__ == "__main__":
    unittest.main()
