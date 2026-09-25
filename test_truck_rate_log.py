import os
import multiprocessing
import tempfile
import unittest
from unittest.mock import patch

import app_v4


def store(areas=None):
    return {"version": 1, "areas": areas or [], "coordinateCache": {}}


def area(name="Chicago", rate="425", locations=None):
    return {"id": "area-1", "name": name, "group": "Midwest", "ftlRate": rate,
            "perKiloRate": "0.85", "locations": locations or []}


def location(location_id="loc-1", zip_code="60605"):
    return {"id": location_id, "city": "Chicago", "state": "IL", "zip": zip_code,
            "lat": 41.87, "lng": -87.62}


def _save_in_process(path, name, start, results):
    app_v4.TRUCK_RATE_DATA_PATH = path
    start.wait(10)
    try:
        saved = app_v4.save_truck_rate_store(store([area(name=name)]), 0, name)
        results.put(saved["revision"] if saved else None)
    except Exception as exc:
        results.put(type(exc).__name__)


class TruckRateLogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(app_v4, "TRUCK_RATE_DATA_PATH",
                                       os.path.join(self.temp.name, "data", "truck_rate.json"))
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.addCleanup(self.temp.cleanup)

    def client(self, user_id="admin", role_id="admin"):
        client = app_v4.app.test_client()
        with client.session_transaction() as session:
            session["authenticated"] = True
            session["user_id"] = user_id
        return client

    def put(self, client, revision, value):
        return client.put("/api/truck-rate/store", json={"revision": revision, "store": value})

    def test_change_details_noop_conflict_and_legacy_file(self):
        client = self.client()
        first = store([area(locations=[location()])])
        self.assertEqual(self.put(client, 0, first).status_code, 200)
        self.assertEqual(self.put(client, 1, first).get_json()["revision"], 1)
        self.assertEqual(self.put(client, 0, store()).status_code, 409)

        changed = store([area(name="Chicago West", rate="450", locations=[location(zip_code="60606")])])
        changed["areas"][0]["group"] = "Central"
        changed["areas"][0]["perKiloRate"] = "0.95"
        self.assertEqual(self.put(client, 1, changed).status_code, 200)
        self.assertEqual(self.put(client, 2, store()).status_code, 200)
        entries = client.get("/api/truck-rate/log").get_json()["entries"]
        self.assertEqual(len(entries), 3)
        self.assertEqual([entry["revision"] for entry in entries], [3, 2, 1])
        self.assertEqual(entries[0]["user_id"], "admin")
        self.assertEqual([change["action"] for change in entries[0]["changes"]], ["deleted", "deleted"])
        self.assertEqual(entries[1]["changes"][0]["before"],
                         {"name": "Chicago", "group": "Midwest", "ftlRate": "425", "perKiloRate": "0.85"})
        self.assertEqual(entries[1]["changes"][0]["after"],
                         {"name": "Chicago West", "group": "Central", "ftlRate": "450", "perKiloRate": "0.95"})
        self.assertEqual(entries[1]["changes"][1]["before"], {"zip": "60605"})
        self.assertEqual(entries[1]["changes"][1]["after"], {"zip": "60606"})
        self.assertEqual([change["action"] for change in entries[2]["changes"]], ["added", "added"])
        self.assertTrue(entries[0]["timestamp"].endswith("+00:00"))

        # The earlier persisted format remains readable and gains history on save.
        with open(app_v4.TRUCK_RATE_DATA_PATH, "w", encoding="utf-8") as handle:
            import json
            json.dump({"schema_version": 1, "revision": 7, "store": first}, handle)
        self.assertEqual(client.get("/api/truck-rate/store").get_json()["revision"], 8)
        self.assertEqual(self.put(client, 8, changed).status_code, 200)
        self.assertEqual(client.get("/api/truck-rate/log").get_json()["total"], 1)

    def test_import_pagination_and_role_access(self):
        with patch.object(app_v4, "get_auth_user", side_effect=lambda user_id: {
            "user_id": user_id, "enabled": True,
            "role_id": "admin" if user_id == "manager" else "truck-rate",
        }):
            manager = self.client("manager")
            editor = self.client("editor", "truck-rate")
            imported = store([area(locations=[location(), location("loc-2", "60607")])])
            self.assertEqual(self.put(editor, 0, imported).status_code, 200)
            self.assertEqual(editor.get("/api/truck-rate/log").status_code, 404)
            self.assertNotIn("Operation Log", editor.get("/features/truck_rate").get_data(as_text=True))
            self.assertEqual(manager.get("/api/truck-rate/log").status_code, 200)
            self.assertIn("Operation Log", manager.get("/features/truck_rate").get_data(as_text=True))
            changes = manager.get("/api/truck-rate/log").get_json()["entries"][0]["changes"]
            self.assertEqual([change["action"] for change in changes], ["added", "added", "added"])
            self.assertEqual(changes[0]["after"]["ftlRate"], "425")
            self.assertEqual(changes[1]["after"]["zip"], "60605")
            self.assertEqual(changes[2]["after"]["zip"], "60607")
            self.assertEqual(manager.get("/api/truck-rate/log?page=2").get_json()["entries"], [])

    def test_city_and_state_are_normalized_for_existing_and_new_data(self):
        client = self.client()
        existing = store([area(locations=[location()])])
        existing["areas"][0]["locations"][0].update({"city": "Chicago ", "state": "il"})
        os.makedirs(os.path.dirname(app_v4.TRUCK_RATE_DATA_PATH), exist_ok=True)
        with open(app_v4.TRUCK_RATE_DATA_PATH, "w", encoding="utf-8") as handle:
            import json
            json.dump({"schema_version": 2, "revision": 7, "store": existing, "history": []}, handle)

        migrated = client.get("/api/truck-rate/store").get_json()
        self.assertEqual(migrated["revision"], 8)
        self.assertEqual(migrated["store"]["areas"][0]["locations"][0]["city"], "CHICAGO")
        self.assertEqual(migrated["store"]["areas"][0]["locations"][0]["state"], "IL")
        self.assertEqual(client.get("/api/truck-rate/store").get_json()["revision"], 8)

        updated = store([area(locations=[location()])])
        updated["areas"][0]["locations"][0].update({"city": "new york", "state": "ny"})
        saved = self.put(client, 8, updated).get_json()
        self.assertEqual(saved["store"]["areas"][0]["locations"][0]["city"], "NEW YORK")
        self.assertEqual(saved["store"]["areas"][0]["locations"][0]["state"], "NY")
        changes = client.get("/api/truck-rate/log").get_json()["entries"][0]["changes"]
        self.assertEqual(changes[0]["after"], {"city": "NEW YORK", "state": "NY"})

    def test_postal_code_cache_is_compatible_and_not_a_business_log_entry(self):
        client = self.client()
        first = store([area(locations=[location()])])
        self.assertEqual(self.put(client, 0, first).status_code, 200)

        cached = store([area(locations=[location()])])
        cached["postalCodePlaceIdCache"] = {"IL|60605": "postal-place-id"}
        saved = self.put(client, 1, cached).get_json()
        self.assertEqual(saved["revision"], 2)
        self.assertEqual(saved["store"]["postalCodePlaceIdCache"], {"IL|60605": "postal-place-id"})
        self.assertEqual(client.get("/api/truck-rate/log").get_json()["total"], 1)

    def test_truck_rate_page_injects_map_id_without_exposing_it_in_assets(self):
        client = self.client()
        values = {"TRUCK_RATE_GOOGLE_MAPS_API_KEY": "browser-key", "TRUCK_RATE_GOOGLE_MAP_ID": "map-id"}
        with patch.object(app_v4, "env_value", side_effect=lambda name, default="": values.get(name, default)):
            page = client.get("/tools/truck-rate/").get_data(as_text=True)
        self.assertIn('window.__TRUCK_RATE_GOOGLE_MAP_ID="map-id"', page)
        self.assertIn('/tools/truck-rate/assets/zip-map.js?v=2', page)
        self.assertIn('/tools/truck-rate/assets/zip-map.css?v=1', page)

    def test_separate_processes_cannot_both_save_stale_revision(self):
        context = multiprocessing.get_context("spawn")
        start = context.Event()
        results = context.Queue()
        workers = [context.Process(target=_save_in_process,
                                   args=(app_v4.TRUCK_RATE_DATA_PATH, name, start, results))
                   for name in ("First", "Second")]
        for worker in workers:
            worker.start()
        start.set()
        outcomes = [results.get(timeout=20) for _ in workers]
        for worker in workers:
            worker.join(20)
            self.assertEqual(worker.exitcode, 0)
        self.assertEqual(sorted(outcomes, key=lambda value: value is None), [1, None])
        self.assertEqual(len(app_v4.load_truck_rate_store()["history"]), 1)

    def test_log_paginates_newest_first(self):
        client = self.client()
        for revision in range(21):
            value = store([area(rate=str(revision))])
            self.assertEqual(self.put(client, revision, value).status_code, 200)
        first = client.get("/api/truck-rate/log?page=1").get_json()
        second = client.get("/api/truck-rate/log?page=2").get_json()
        self.assertEqual((first["total"], len(first["entries"]), len(second["entries"])), (21, 20, 1))
        self.assertEqual((first["entries"][0]["revision"], second["entries"][0]["revision"]), (21, 1))


if __name__ == "__main__":
    unittest.main()
