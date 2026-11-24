"""End-to-end HTTP tests: boot a real server on an ephemeral port and talk to
it over sockets, so routing, encoding and JSON handling are all exercised."""

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from chizhang.server import make_server
from chizhang.store import Store


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.store = Store(Path(cls.tmp.name) / "store.json")
        cls.server = make_server("127.0.0.1", 0, cls.store, quiet=True)
        cls.port = cls.server.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    # -- helpers -------------------------------------------------------
    def request(self, path, method="GET", payload=None):
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req) as res:
            body = res.read()
            return res.status, json.loads(body) if body else None

    def request_error(self, path, method="GET", payload=None):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.request(path, method, payload)
        return ctx.exception.code, json.loads(ctx.exception.read())

    # -- static --------------------------------------------------------
    def test_index_served(self):
        with urllib.request.urlopen(self.base + "/") as res:
            html = res.read().decode()
        self.assertEqual(res.status, 200)
        self.assertIn("吃账", html)

    def test_assets_served(self):
        for name, marker in (("style.css", "--accent"), ("app.js", "renderSummary")):
            with self.subTest(asset=name), urllib.request.urlopen(f"{self.base}/assets/{name}") as res:
                self.assertEqual(res.status, 200)
                self.assertIn(marker, res.read().decode())

    def test_path_traversal_blocked(self):
        for path in ("/assets/../server.py", "/assets/%2e%2e/server.py", "/nope"):
            with self.subTest(path=path):
                code, _ = self.request_error(path)
                self.assertIn(code, (403, 404))

    # -- state ---------------------------------------------------------
    def test_state_defaults(self):
        status, data = self.request("/api/state?date=2026-11-24")
        self.assertEqual(status, 200)
        self.assertEqual(data["date"], "2026-11-24")
        self.assertEqual(len(data["meals"]), 4)
        self.assertIn("targets", data)
        self.assertGreater(data["food_stats"]["total"], 100)
        self.assertIn("categories", data)

    def test_state_rejects_bad_date(self):
        code, body = self.request_error("/api/state?date=2026-99-99")
        self.assertEqual(code, 400)
        self.assertIn("日期", body["error"])

    # -- food search ---------------------------------------------------
    def test_food_search_chinese(self):
        q = urllib.parse.quote("鸡蛋")
        _, data = self.request(f"/api/foods?q={q}")
        self.assertTrue(data["items"])
        self.assertEqual(data["items"][0]["name"], "鸡蛋")

    def test_food_search_raw_utf8(self):
        # curl sends unencoded bytes; the server must cope.
        _, data = self.request("/api/foods?q=%E7%B1%B3%E9%A5%AD")
        self.assertEqual(data["items"][0]["name"], "米饭")

    def test_food_search_by_category(self):
        q = urllib.parse.quote("水果")
        _, data = self.request(f"/api/foods?category={q}")
        self.assertTrue(all(f["category"] == "水果" for f in data["items"]))

    # -- profile & entries ---------------------------------------------
    def test_profile_updates_targets(self):
        _, before = self.request("/api/state?date=2026-11-24")
        old_kcal = before["targets"]["kcal"]
        _, data = self.request("/api/profile", "POST", {
            "sex": "male", "age": 28, "height_cm": 178, "weight_kg": 80,
            "activity": "moderate", "goal": "lose", "deficit": 500,
            "date": "2026-11-24",
        })
        self.assertEqual(data["profile"]["sex"], "male")
        self.assertGreater(data["targets"]["kcal"], old_kcal)
        self.assertEqual(data["targets"]["protein"], round(1.6 * 80))

    def test_add_and_delete_entry(self):
        _, state = self.request("/api/state?date=2026-11-24")
        food = state["food_stats"]
        self.assertGreater(food["total"], 0)

        _, foods = self.request("/api/foods?q=" + urllib.parse.quote("米饭"))
        rice = foods["items"][0]

        _, res = self.request("/api/entries", "POST", {
            "date": "2026-11-24", "meal": "lunch", "food_id": rice["id"],
            "grams": 150, "unit_label": "碗",
        })
        entry = res["entry"]
        self.assertEqual(entry["name"], "米饭")
        self.assertAlmostEqual(entry["kcal"], 174.0, delta=1)
        self.assertEqual(entry["unit_grams"], 150)
        self.assertEqual(res["state"]["day"]["entry_count"], 1)

        _, after = self.request(f"/api/entries/{entry['id']}", "DELETE")
        self.assertEqual(after["day"]["entry_count"], 0)

    def test_add_entry_validation(self):
        code, body = self.request_error("/api/entries", "POST", {"food_id": "f999", "grams": 100})
        self.assertEqual(code, 404)
        self.assertIn("找不到", body["error"])

        code, _ = self.request_error("/api/entries", "POST", {
            "date": "2026-11-24", "meal": "lunch", "food_id": "f000", "grams": 99999,
        })
        self.assertEqual(code, 400)

        code, _ = self.request_error("/api/entries", "POST", {
            "date": "2026-11-24", "meal": "brunch", "food_id": "f000", "grams": 100,
        })
        self.assertEqual(code, 400)

    def test_custom_entry(self):
        _, res = self.request("/api/entries", "POST", {
            "date": "2026-11-22", "meal": "dinner",
            "custom": {"name": "妈妈做的红烧肉", "grams": 200, "kcal": 700,
                       "protein": 20, "carb": 10, "fat": 60},
        })
        entry = res["entry"]
        self.assertEqual(entry["name"], "妈妈做的红烧肉")
        self.assertEqual(entry["kcal"], 700)
        self.assertIsNone(entry["food_id"])

    def test_custom_entry_requires_name(self):
        code, _ = self.request_error("/api/entries", "POST", {
            "date": "2026-11-22", "meal": "dinner",
            "custom": {"grams": 100, "kcal": 300},
        })
        self.assertEqual(code, 400)

    def test_delete_missing_entry(self):
        code, _ = self.request_error("/api/entries/deadbeef", "DELETE")
        self.assertEqual(code, 404)

    # -- weight --------------------------------------------------------
    def test_weight_flow(self):
        _, data = self.request("/api/weights", "POST", {"date": "2026-11-24", "kg": 57.4})
        self.assertEqual(data["weight"]["latest"]["kg"], 57.4)
        self.assertAlmostEqual(data["profile"]["weight_kg"], 57.4, places=1)

        _, data = self.request("/api/weights/2026-11-24", "DELETE")
        self.assertNotEqual(
            (data["weight"]["latest"] or {}).get("kg"), 57.4,
        )

    def test_weight_validation(self):
        code, _ = self.request_error("/api/weights", "POST", {"date": "2026-11-24", "kg": 900})
        self.assertEqual(code, 400)

    # -- report / export / import --------------------------------------
    def test_report(self):
        _, data = self.request("/api/report?days=7&date=2026-11-24")
        self.assertEqual(len(data["days"]), 7)
        self.assertIn("avg_kcal", data)

    def test_export_contains_schema(self):
        _, data = self.request("/api/export")
        self.assertEqual(data["app"], "chizhang")
        for key in ("entries", "weights", "profile"):
            self.assertIn(key, data)

    def test_import_replaces_data(self):
        payload = {
            "profile": {"sex": "female", "weight_kg": 52},
            "entries": [{
                "id": "abc123", "date": "2026-11-20", "meal": "lunch",
                "name": "导入的食物", "category": "主食", "grams": 100,
                "kcal": 250, "protein": 5, "carb": 40, "fat": 6, "ts": 1,
            }],
            "weights": [],
        }
        _, data = self.request("/api/import", "POST", payload)
        self.assertEqual(data["profile"]["weight_kg"], 52)
        _, state = self.request("/api/state?date=2026-11-20")
        self.assertEqual(state["day"]["entry_count"], 1)

    def test_import_rejects_junk(self):
        code, _ = self.request_error("/api/import", "POST", {"nope": 1})
        self.assertEqual(code, 400)

    def test_clear_day(self):
        self.request("/api/entries", "POST", {
            "date": "2026-11-21", "meal": "lunch", "food_id": "f000", "grams": 100,
        })
        _, data = self.request("/api/day/2026-11-21", "DELETE")
        self.assertEqual(data["day"]["entry_count"], 0)

    # -- misc ----------------------------------------------------------
    def test_unknown_api_route(self):
        code, body = self.request_error("/api/does-not-exist")
        self.assertEqual(code, 404)
        self.assertIn("没有这个接口", body["error"])

    def test_malformed_json(self):
        req = urllib.request.Request(
            self.base + "/api/entries", data=b"{not json",
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 400)
        self.assertIn("JSON", json.loads(ctx.exception.read())["error"])

    def test_recent_foods_appear_after_logging(self):
        self.request("/api/entries", "POST", {
            "date": "2026-11-24", "meal": "breakfast", "food_id": "f001", "grams": 100,
        })
        _, state = self.request("/api/state?date=2026-11-24")
        self.assertTrue(any(f["id"] == "f001" for f in state["recent"]))


if __name__ == "__main__":
    unittest.main()
