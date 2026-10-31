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

NYC = (40.7128, -74.0060)
HAMPTONS = (40.9634, -72.1848)  # ~97 mi from NYC
NEWARK = (40.7357, -74.1724)  # ~9 mi from NYC
LA = (34.0522, -118.2437)


def make_cfg(shared_account=False, **overrides):
    data = {
        "radius_miles": 30,
        "hysteresis_miles": 5,
        "accounts": {
            "a": {"email": "a@example.com", "password": "x"},
            "b": {"email": "b@example.com", "password": "y"},
        },
        "beds": [
            {"name": "City", "lat": NYC[0], "lon": NYC[1], "account": "a", "device_id": "dev-city"},
            {
                "name": "Beach",
                "lat": HAMPTONS[0],
                "lon": HAMPTONS[1],
                "account": "a" if shared_account else "b",
                "device_id": "dev-beach",
            },
        ],
    }
    data.update(overrides)
    cfg = config.parse(data)
    cfg.state_file = os.path.join(tempfile.mkdtemp(), "state.json")
    return cfg


class FakeClient:
    def __init__(self, log, key, fail=False):
        self.log, self.key, self.fail = log, key, fail

    def set_away(self, away):
        if self.fail:
            raise RuntimeError("boom")
        self.log.append((self.key, "away" if away else "home"))

    def set_current_device(self, device_id, side):
        self.log.append((self.key, "device", device_id))


def controller(cfg, failing=()):
    calls = []
    ctl = Controller(cfg, lambda acct: FakeClient(calls, acct.key, acct.key in failing))
    return ctl, calls


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

    def test_hysteresis(self):
        cfg = make_cfg()
        # ~32 miles from NYC: outside 30 mi radius but inside 30+5 hysteresis band.
        point = (40.7128, -74.0060 + 32 / 52.4)
        d = distance_miles(*NYC, *point)
        self.assertTrue(30 < d < 35, d)
        self.assertIsNone(decide(cfg, *point).home)
        self.assertEqual(decide(cfg, *point, previous_home="City").home, "City")

    def test_nearest_wins_when_radii_overlap(self):
        cfg = make_cfg(radius_miles=200)
        self.assertEqual(decide(cfg, *NEWARK).home, "City")


class ControllerTest(unittest.TestCase):
    def test_separate_accounts(self):
        ctl, calls = controller(make_cfg())
        r = ctl.update(*NEWARK)
        self.assertEqual(r.home, "City")
        self.assertCountEqual(calls, [("a", "home"), ("b", "away")])

        calls.clear()
        ctl.update(*NYC)  # nothing changed -> no API calls
        self.assertEqual(calls, [])

        ctl.update(*HAMPTONS)
        self.assertCountEqual(calls, [("a", "away"), ("b", "home")])

        calls.clear()
        ctl.update(*LA)
        self.assertCountEqual(calls, [("b", "away")])  # a already away

    def test_unchanged_when_far_from_all(self):
        ctl, calls = controller(make_cfg(when_away_from_all="unchanged"))
        ctl.update(*NYC)
        calls.clear()
        ctl.update(*LA)
        self.assertEqual(calls, [])

    def test_shared_account_switches_device(self):
        ctl, calls = controller(make_cfg(shared_account=True))
        ctl.update(*NYC)
        self.assertEqual(calls, [("a", "device", "dev-city"), ("a", "home")])
        calls.clear()
        ctl.update(*HAMPTONS)
        self.assertEqual(calls, [("a", "device", "dev-beach")])
        calls.clear()
        ctl.update(*LA)
        self.assertEqual(calls, [("a", "away")])

    def test_shared_account_requires_device_ids(self):
        data = {
            "accounts": {"a": {"email": "a@example.com", "password": "x"}},
            "beds": [
                {"name": "One", "lat": 0, "lon": 0, "account": "a"},
                {"name": "Two", "lat": 1, "lon": 1, "account": "a"},
            ],
        }
        with self.assertRaises(config.ConfigError):
            config.parse(data)

    def test_failure_is_retried(self):
        cfg = make_cfg()
        ctl, calls = controller(cfg, failing={"b"})
        r = ctl.update(*NYC)
        self.assertEqual(len(r.errors), 1)
        self.assertEqual(calls, [("a", "home")])
        # Next update with a working client retries b only.
        ctl2, calls2 = controller(cfg)
        ctl2.update(*NYC)
        self.assertEqual(calls2, [("b", "away")])

    def test_dry_run_changes_nothing(self):
        ctl, calls = controller(make_cfg())
        r = ctl.update(*NYC, dry_run=True)
        self.assertEqual(calls, [])
        self.assertEqual(len(r.actions), 2)
        self.assertEqual(ctl.status(), {})

    def test_poor_accuracy_skipped(self):
        ctl, calls = controller(make_cfg(max_accuracy_miles=2))
        r = ctl.update(*NYC, accuracy_miles=10)
        self.assertIsNotNone(r.skipped)
        self.assertEqual(calls, [])

    def test_password_env(self):
        os.environ["TEST_PW"] = "secret"
        cfg = make_cfg(accounts={"a": {"email": "e", "password_env": "TEST_PW"},
                                 "b": {"email": "f", "password_env": "TEST_PW"}})
        self.assertEqual(cfg.accounts["a"].password, "secret")


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
        self.assertCountEqual(self.calls, [("a", "home"), ("b", "away")])

    def test_owntracks(self):
        self.assertEqual(self.post({"_type": "location", "lat": HAMPTONS[0], "lon": HAMPTONS[1], "acc": 20}), [])
        self.assertEqual(self.post({"_type": "transition"}), [])
        self.assertIn(("b", "home"), self.calls)

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
