import json
import os
import multiprocessing
import tempfile
import unittest
from unittest.mock import patch

import app_v4


ZONE_SET_ID = "zoneset-1"


def origin(origin_id="origin-1", name="Bensenville", zone_set_id=ZONE_SET_ID):
    return {"id": origin_id, "name": name, "street": "1301 Devon Ave.", "city": "BENSENVILLE", "state": "IL",
            "zip": "60106", "lat": 41.99, "lng": -87.94, "zoneSetId": zone_set_id}


def store(areas=None, rates=None, origins=None):
    return {"version": 2, "origins": origins if origins is not None else [origin()],
            "zoneSets": [{"id": ZONE_SET_ID, "name": "Main"}], "areas": areas or [],
            "rates": rates or [], "coordinateCache": {}}


def area(name="Chicago", locations=None, area_id="area-1"):
    return {"id": area_id, "zoneSetId": ZONE_SET_ID, "name": name, "group": "Midwest",
            "locations": locations or []}


def rate(ftl="425", per_kilo="0.85", origin_id="origin-1", area_id="area-1", start="", end="", rate_id=None):
    return {"id": rate_id or "rate-{0}-{1}".format(origin_id, area_id), "originId": origin_id, "areaId": area_id,
            "ftlRate": ftl, "perKiloRate": per_kilo, "effectiveFrom": start, "effectiveTo": end}


def location(location_id="loc-1", zip_code="60605"):
    return {"id": location_id, "city": "Chicago", "state": "IL", "zip": zip_code,
            "lat": 41.87, "lng": -87.62}


def legacy_store(rate_value="425"):
    return {"version": 1, "coordinateCache": {}, "areas": [
        {"id": "area-1", "name": "IL - ZONE 1", "group": "IL", "ftlRate": rate_value, "perKiloRate": "0.2",
         "locations": [location()]},
        {"id": "area-2", "name": "IL - ZONE 2", "group": "IL", "ftlRate": "", "perKiloRate": "",
         "locations": []}]}


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

    def write_file(self, revision, value, schema_version=2):
        os.makedirs(os.path.dirname(app_v4.TRUCK_RATE_DATA_PATH), exist_ok=True)
        with open(app_v4.TRUCK_RATE_DATA_PATH, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": schema_version, "revision": revision, "store": value, "history": []}, handle)

    def test_change_details_noop_conflict_and_deletions(self):
        client = self.client()
        first = store([area(locations=[location()])], [rate()])
        self.assertEqual(self.put(client, 0, first).status_code, 200)
        self.assertEqual(self.put(client, 1, first).get_json()["revision"], 1)
        self.assertEqual(self.put(client, 0, store()).status_code, 409)

        changed = store([area(name="Chicago West", locations=[location(zip_code="60606")])], [rate("450", "0.95")])
        changed["areas"][0]["group"] = "Central"
        self.assertEqual(self.put(client, 1, changed).status_code, 200)
        self.assertEqual(self.put(client, 2, store()).status_code, 200)
        entries = client.get("/api/truck-rate/log").get_json()["entries"]
        self.assertEqual(len(entries), 3)
        self.assertEqual([entry["revision"] for entry in entries], [3, 2, 1])
        self.assertEqual(entries[0]["user_id"], "admin")
        # The rate disappears together with its area, so only the area and ZIP are logged.
        self.assertEqual([(change["entity"], change["action"]) for change in entries[0]["changes"]],
                         [("area", "deleted"), ("location", "deleted")])
        self.assertEqual([change["entity"] for change in entries[1]["changes"]], ["area", "location", "rate"])
        self.assertEqual(entries[1]["changes"][0]["before"], {"name": "Chicago", "group": "Midwest"})
        self.assertEqual(entries[1]["changes"][0]["after"], {"name": "Chicago West", "group": "Central"})
        self.assertEqual(entries[1]["changes"][1]["before"], {"zip": "60605"})
        self.assertEqual(entries[1]["changes"][1]["after"], {"zip": "60606"})
        self.assertEqual(entries[1]["changes"][2]["before"], {"ftlRate": "425", "perKiloRate": "0.85"})
        self.assertEqual(entries[1]["changes"][2]["after"], {"ftlRate": "450", "perKiloRate": "0.95"})
        self.assertEqual(entries[1]["changes"][2]["origin"], "Bensenville")
        self.assertEqual(entries[1]["changes"][2]["area"], "Chicago West")
        self.assertEqual([change["action"] for change in entries[2]["changes"]], ["added"] * 5)
        self.assertEqual([change["entity"] for change in entries[2]["changes"]],
                         ["zoneSet", "origin", "area", "location", "rate"])
        self.assertTrue(entries[0]["timestamp"].endswith("+00:00"))

    def test_single_pickup_file_is_migrated_once_with_a_backup(self):
        client = self.client()
        self.write_file(7, legacy_store(), schema_version=1)
        migrated = client.get("/api/truck-rate/store").get_json()
        self.assertEqual(migrated["revision"], 8)
        saved = migrated["store"]
        self.assertEqual([item["name"] for item in saved["origins"]], ["Bensenville"])
        self.assertEqual(saved["origins"][0]["street"], "1301 Devon Ave.")
        self.assertEqual((saved["origins"][0]["city"], saved["origins"][0]["state"], saved["origins"][0]["zip"]),
                         ("BENSENVILLE", "IL", "60106"))
        self.assertEqual(saved["zoneSets"], [{"id": app_v4.DEFAULT_ZONE_SET_ID, "name": "Bensenville"}])
        self.assertTrue(all(item["zoneSetId"] == app_v4.DEFAULT_ZONE_SET_ID for item in saved["areas"]))
        self.assertTrue(all("ftlRate" not in item and "perKiloRate" not in item for item in saved["areas"]))
        # Only the area that actually had a rate gets a rate row.
        self.assertEqual(saved["rates"], [{"id": "rate-{0}-area-1".format(app_v4.DEFAULT_ORIGIN_ID),
                                           "originId": app_v4.DEFAULT_ORIGIN_ID, "areaId": "area-1",
                                           "ftlRate": "425", "perKiloRate": "0.2", "effectiveFrom": "", "effectiveTo": ""}])
        backup = os.path.join(os.path.dirname(app_v4.TRUCK_RATE_DATA_PATH), "truck_rate_data.pre-origins.json")
        with open(backup, "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["store"]["areas"][0]["ftlRate"], "425")
        self.assertEqual(client.get("/api/truck-rate/store").get_json()["revision"], 8)
        self.assertEqual(client.get("/api/truck-rate/log").get_json()["total"], 0)

    def test_single_pickup_data_may_seed_an_empty_server_only(self):
        client = self.client()
        seeded = self.put(client, 0, legacy_store())
        self.assertEqual(seeded.status_code, 200)
        self.assertEqual(seeded.get_json()["store"]["origins"][0]["name"], "Bensenville")
        self.assertEqual(seeded.get_json()["store"]["rates"][0]["ftlRate"], "425")
        stale = self.put(client, 1, legacy_store("999"))
        self.assertEqual(stale.status_code, 400)
        self.assertEqual(client.get("/api/truck-rate/store").get_json()["store"]["rates"][0]["ftlRate"], "425")

    def test_invalid_pickups_zone_sets_and_rates_are_rejected(self):
        client = self.client()
        good_area = area(locations=[location()])
        bad_values = {
            "unknown area": store([good_area], [rate(area_id="missing")]),
            "unknown pickup": store([good_area], [rate(origin_id="missing")]),
            "duplicate rate": store([good_area], [rate(), rate("1", "2")]),
            "negative rate": store([good_area], [rate("-1")]),
            "text rate": store([good_area], [rate("abc")]),
            "duplicate pickup name": store([good_area], origins=[origin(), origin("origin-2", " bensenville ")]),
            "blank pickup name": store([good_area], origins=[origin(name="  ")]),
            "unknown pickup zone set": store([good_area], origins=[origin(zone_set_id="missing")]),
            "unknown area zone set": store([{**good_area, "zoneSetId": "missing"}]),
        }
        for label, value in bad_values.items():
            with self.subTest(label):
                self.assertEqual(self.put(client, 0, value).status_code, 400)
        other_zone_set = store([good_area], [rate()])
        other_zone_set["zoneSets"].append({"id": "zoneset-2", "name": "Dallas"})
        other_zone_set["origins"].append(origin("origin-2", "Dallas", "zoneset-2"))
        other_zone_set["rates"].append(rate(origin_id="origin-2"))
        self.assertEqual(self.put(client, 0, other_zone_set).status_code, 400)
        self.assertFalse(os.path.exists(app_v4.TRUCK_RATE_DATA_PATH))

    def test_pickup_edits_and_deletion_are_logged(self):
        client = self.client()
        shared = store([area(locations=[location()])], [rate(), rate("500", "1", "origin-2")],
                       origins=[origin(), origin("origin-2", "Chicago South")])
        self.assertEqual(self.put(client, 0, shared).status_code, 200)

        edited = json.loads(json.dumps(shared))
        edited["origins"][0].update({"name": " Bensenville DC ", "street": "500 Main St.", "city": "elk grove", "state": "il",
                                     "lat": 42.0, "lng": -88.0})
        saved = self.put(client, 1, edited).get_json()["store"]
        self.assertEqual(saved["origins"][0]["name"], "Bensenville DC")
        self.assertEqual((saved["origins"][0]["city"], saved["origins"][0]["state"]), ("ELK GROVE", "IL"))
        entry = client.get("/api/truck-rate/log").get_json()["entries"][0]
        self.assertEqual([(change["entity"], change["action"]) for change in entry["changes"]], [("origin", "updated")])
        self.assertEqual(entry["changes"][0]["before"],
                         {"name": "Bensenville", "street": "1301 Devon Ave.", "city": "BENSENVILLE"})
        self.assertEqual(entry["changes"][0]["after"],
                         {"name": "Bensenville DC", "street": "500 Main St.", "city": "ELK GROVE"})

        removed = json.loads(json.dumps(saved))
        removed["origins"].pop()
        removed["rates"].pop()
        self.assertEqual(self.put(client, 2, removed).status_code, 200)
        entry = client.get("/api/truck-rate/log").get_json()["entries"][0]
        self.assertEqual([(change["entity"], change["action"], change.get("origin")) for change in entry["changes"]],
                         [("origin", "deleted", "Chicago South")])

        coordinates_only = json.loads(json.dumps(removed))
        coordinates_only["origins"][0].update({"lat": 1.0, "lng": 2.0})
        self.assertEqual(self.put(client, 3, coordinates_only).get_json()["revision"], 4)
        self.assertEqual(client.get("/api/truck-rate/log").get_json()["total"], 3)

    def test_rates_without_validity_periods_are_upgraded_as_open_ended(self):
        client = self.client()
        before = store([area(locations=[location()])], [rate()])
        for item in before["rates"]:
            for field in ("id", "effectiveFrom", "effectiveTo"):
                item.pop(field)
        before["version"] = 2
        self.write_file(3, before)
        upgraded = client.get("/api/truck-rate/store").get_json()
        self.assertEqual(upgraded["revision"], 4)
        self.assertEqual(upgraded["store"]["rates"], [rate()])
        self.assertEqual(upgraded["store"]["version"], 3)
        folder = os.path.dirname(app_v4.TRUCK_RATE_DATA_PATH)
        with open(os.path.join(folder, "truck_rate_data.pre-periods.json"), "r", encoding="utf-8") as handle:
            self.assertNotIn("id", json.load(handle)["store"]["rates"][0])
        self.assertEqual(client.get("/api/truck-rate/store").get_json()["revision"], 4)
        self.assertEqual(client.get("/api/truck-rate/log").get_json()["total"], 0)

    def test_validity_periods_are_validated(self):
        client = self.client()
        a = area(locations=[location()])
        invalid = {
            "start after end": [rate(start="2026-11-01", end="2026-10-01")],
            "bad date format": [rate(start="10/01/2026")],
            "impossible date": [rate(end="2026-02-30")],
            "overlap": [rate(rate_id="r1", start="2026-01-01", end="2026-06-30"), rate(rate_id="r2", start="2026-06-30")],
            "open ended overlap": [rate(rate_id="r1", start="2026-01-01"), rate(rate_id="r2", start="2027-01-01", end="2027-06-30")],
            "no prices": [rate("", "")],
        }
        for label, rates in invalid.items():
            with self.subTest(label):
                self.assertEqual(self.put(client, 0, store([a], rates)).status_code, 400)
        history = [rate(rate_id="r1", start="", end="2026-09-30"), rate("450", "0.9", rate_id="r2", start="2026-10-01"),
                   rate("500", "1", origin_id="origin-1", rate_id="r3", start="", end="")]
        self.assertEqual(self.put(client, 0, store([a], history[:2])).status_code, 200)
        self.assertEqual(self.put(client, 1, store([a], history)).status_code, 400)
        entry = client.get("/api/truck-rate/log").get_json()["entries"][0]
        added = [change for change in entry["changes"] if change["entity"] == "rate"]
        self.assertEqual([change["after"]["effectiveFrom"] for change in added], ["", "2026-10-01"])

        # Extending the end date of a running period is a logged, targeted change.
        extended = store([a], [rate(rate_id="r1", start="", end="2026-09-30"), rate("450", "0.9", rate_id="r2", start="2026-10-01", end="2026-12-31")])
        self.assertEqual(self.put(client, 1, extended).status_code, 200)
        entry = client.get("/api/truck-rate/log").get_json()["entries"][0]
        self.assertEqual([(change["entity"], change["action"], change["before"], change["after"]) for change in entry["changes"]],
                         [("rate", "updated", {"effectiveTo": ""}, {"effectiveTo": "2026-12-31"})])

    def test_ended_periods_can_only_be_changed_by_administrators(self):
        with patch.object(app_v4, "_truck_rate_today", return_value=__import__("datetime").date(2026, 10, 8)), \
                patch.object(app_v4, "get_auth_user", side_effect=lambda user_id: {
                    "user_id": user_id, "enabled": True,
                    "role_id": "admin" if user_id == "manager" else "truck-rate"}):
            manager = self.client("manager")
            editor = self.client("editor", "truck-rate")
            a = area(locations=[location()])
            ended = rate(rate_id="ended", start="2026-01-01", end="2026-09-30")
            active = rate("450", "0.9", rate_id="active", start="2026-10-01")
            self.assertEqual(self.put(manager, 0, store([a], [ended, active])).status_code, 200)

            repriced = rate("999", "0.85", rate_id="ended", start="2026-01-01", end="2026-09-30")
            denied = self.put(editor, 1, store([a], [repriced, active]))
            self.assertEqual(denied.status_code, 403)
            self.assertIn("administrator", denied.get_json()["error"])
            self.assertEqual(self.put(editor, 1, store([a], [active])).status_code, 403)

            # Editors can still adjust running periods and add new ones after an ended one.
            renewed = rate("450", "0.9", rate_id="active", start="2026-10-01", end="2026-12-31")
            newer = rate("470", "0.95", rate_id="next", start="2027-01-01")
            self.assertEqual(self.put(editor, 1, store([a], [ended, renewed, newer])).status_code, 200)
            # Deleting a whole area is a separate decision and removes its rates with it.
            self.assertEqual(self.put(editor, 2, store([])).status_code, 200)

            self.assertEqual(self.put(manager, 3, store([a], [ended])).status_code, 200)
            self.assertEqual(self.put(manager, 4, store([a], [repriced])).status_code, 200)
            self.assertEqual(self.put(manager, 5, store([a])).status_code, 200)
    def test_import_pagination_and_role_access(self):
        with patch.object(app_v4, "get_auth_user", side_effect=lambda user_id: {
            "user_id": user_id, "enabled": True,
            "role_id": "admin" if user_id == "manager" else "truck-rate",
        }):
            manager = self.client("manager")
            editor = self.client("editor", "truck-rate")
            imported = store([area(locations=[location(), location("loc-2", "60607")])], [rate()])
            self.assertEqual(self.put(editor, 0, imported).status_code, 200)
            self.assertEqual(editor.get("/api/truck-rate/log").status_code, 404)
            self.assertNotIn("Operation Log", editor.get("/features/truck_rate").get_data(as_text=True))
            self.assertEqual(manager.get("/api/truck-rate/log").status_code, 200)
            self.assertIn("Operation Log", manager.get("/features/truck_rate").get_data(as_text=True))
            changes = manager.get("/api/truck-rate/log").get_json()["entries"][0]["changes"]
            self.assertEqual({change["action"] for change in changes}, {"added"})
            self.assertEqual([change["entity"] for change in changes],
                             ["zoneSet", "origin", "area", "location", "location", "rate"])
            self.assertEqual(changes[1]["after"]["street"], "1301 Devon Ave.")
            self.assertEqual(changes[3]["after"]["zip"], "60605")
            self.assertEqual(changes[4]["after"]["zip"], "60607")
            self.assertEqual(changes[5]["after"]["ftlRate"], "425")
            self.assertEqual(manager.get("/api/truck-rate/log?page=2").get_json()["entries"], [])

    def test_city_and_state_are_normalized_for_existing_and_new_data(self):
        client = self.client()
        existing = store([area(locations=[location()])])
        existing["areas"][0]["locations"][0].update({"city": "Chicago ", "state": "il"})
        self.write_file(7, existing)

        migrated = client.get("/api/truck-rate/store").get_json()
        self.assertEqual(migrated["revision"], 8)
        self.assertEqual(migrated["store"]["areas"][0]["locations"][0]["city"], "CHICAGO")
        self.assertEqual(migrated["store"]["areas"][0]["locations"][0]["state"], "IL")
        self.assertEqual(client.get("/api/truck-rate/store").get_json()["revision"], 8)
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(app_v4.TRUCK_RATE_DATA_PATH),
                                                     "truck_rate_data.pre-origins.json")))

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
        self.assertIn('/tools/truck-rate/assets/zip-map.js?v=', page)
        self.assertIn('/tools/truck-rate/assets/zip-map.css?v=', page)

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
            value = store([area()], [rate(ftl=str(revision))])
            self.assertEqual(self.put(client, revision, value).status_code, 200)
        first = client.get("/api/truck-rate/log?page=1").get_json()
        second = client.get("/api/truck-rate/log?page=2").get_json()
        self.assertEqual((first["total"], len(first["entries"]), len(second["entries"])), (21, 20, 1))
        self.assertEqual((first["entries"][0]["revision"], second["entries"][0]["revision"]), (21, 1))

    def test_zips_within_returns_nearby_zips_nearest_first(self):
        client = self.client()
        response = client.get("/api/truck-rate/zips-within?lat=41.99305&lng=-87.9615&miles=25")
        self.assertEqual(response.status_code, 200)
        zips = response.get_json()["zips"]
        self.assertIn("60106", [item["zip"] for item in zips])
        distances = [item["distance"] for item in zips]
        self.assertEqual(distances, sorted(distances))
        self.assertTrue(all(value <= 25 for value in distances))
        wider = client.get("/api/truck-rate/zips-within?lat=41.99305&lng=-87.9615&miles=50").get_json()["zips"]
        self.assertGreater(len(wider), len(zips))

    def test_radius_presets_are_saved_and_validated(self):
        client = self.client()
        good = store()
        good["radiusPresets"] = {"pickup:origin-1": [10, 30, 60]}
        saved = self.put(client, 0, good)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(client.get("/api/truck-rate/store").get_json()["store"]["radiusPresets"], {"pickup:origin-1": [10, 30, 60]})
        for bad in ([0], [251], [12.5], ["10"], [True], list(range(1, 12)), "10"):
            value = store()
            value["radiusPresets"] = {"pickup:origin-1": bad}
            self.assertEqual(self.put(client, 1, value).status_code, 400, bad)
        value = store()
        value["radiusPresets"] = []
        self.assertEqual(self.put(client, 1, value).status_code, 400)

    def places(self, query):
        response = self.client().get("/api/truck-rate/places?" + query)
        return response.status_code, (response.get_json() or {})

    def test_places_lookup_by_city_state_or_zip(self):
        status, body = self.places("city=Naperville&state=IL")
        self.assertEqual(status, 200)
        zips = {item["zip"] for item in body["places"]}
        self.assertTrue({"60540", "60563", "60564", "60565"} <= zips)
        self.assertTrue(all(item["city"] == "NAPERVILLE" and item["state"] == "IL" for item in body["places"]))
        status, body = self.places("zip=60540")
        self.assertEqual([(item["state"], item["zip"]) for item in body["places"]][0], ("IL", "60540"))
        status, body = self.places("city=springfield")
        self.assertGreater(len({item["state"] for item in body["places"]}), 3)
        self.assertEqual(self.places("city=springfield&state=MO")[1]["places"][0]["state"], "MO")

    def test_places_lookup_matches_saint_and_lists_every_city_for_a_zip(self):
        saint = {item["state"] for item in self.places("city=St.%20Louis")[1]["places"]}
        self.assertIn("MO", saint)
        shared = self.places("zip=60564")[1]["places"]
        self.assertEqual(shared[0]["primary"], True)
        self.assertGreater(len({item["city"] for item in shared}), 1)

    def test_places_lookup_rejects_bad_input_and_suggests_cities(self):
        for query in ("", "state=IL", "zip=123", "city=x&state=ILL"):
            self.assertEqual(self.places(query)[0], 400, query)
        self.assertEqual(self.places("zip=00000")[1]["places"], [])
        cities = self.client().get("/api/truck-rate/city-suggest?q=napervi").get_json()["cities"]
        self.assertEqual(cities[0], {"city": "NAPERVILLE", "states": ["IL"]})
        self.assertEqual(self.client().get("/api/truck-rate/city-suggest?q=n").get_json()["cities"], [])

    def test_a_place_may_not_be_saved_twice_in_one_zone_set(self):
        client = self.client()

        def place(location_id, city, zip_code="60564"):
            return {"id": location_id, "city": city, "state": "IL", "zip": zip_code}

        two_cities = store([area(locations=[place("a", "NAPERVILLE"), place("b", "AURORA")])])
        self.assertEqual(self.put(client, 0, two_cities).status_code, 200)
        same_area = store([area(locations=[place("a", "NAPERVILLE"), place("b", "naperville")])])
        self.assertEqual(self.put(client, 1, same_area).status_code, 400)
        other_area = store([area(locations=[place("a", "ST. LOUIS", "63101")]),
                            area("Second", [place("b", "Saint Louis", "63101")], "area-2")])
        self.assertEqual(self.put(client, 1, other_area).status_code, 400)

    def test_radius_remark_is_recorded_once_and_cannot_be_changed(self):
        client = self.client()
        radius = {"center": "pickup:origin-1", "text": "Center: Bensenville | Radius: 25 mi | Ring: 0-25 mi",
                  "from": 0, "to": 25, "radii": [25]}
        created = area(name="LAX_HAWTHORN")
        created["radius"] = radius
        created["remark"] = "Called carrier"
        self.assertEqual(self.put(client, 0, store([created])).status_code, 200)

        # The user's own note may change; the recorded radius line may not.
        noted = json.loads(json.dumps(created))
        noted["remark"] = "Rates agreed"
        saved = self.put(client, 1, store([noted]))
        self.assertEqual(saved.status_code, 200)
        entries = client.get("/api/truck-rate/log").get_json()["entries"]
        self.assertEqual(entries[0]["changes"][0]["after"], {"remark": "Rates agreed"})

        for tampered in ({**noted, "radius": {**radius, "text": "edited"}}, {k: v for k, v in noted.items() if k != "radius"}):
            rejected = self.put(client, 2, store([tampered]))
            self.assertEqual(rejected.status_code, 400)
            self.assertIn("radius remark", rejected.get_json()["error"])

        # Deleting the whole area is still allowed.
        self.assertEqual(self.put(client, 2, store([])).status_code, 200)

    def test_new_area_names_must_be_unique_in_a_zone_set(self):
        client = self.client()
        self.assertEqual(self.put(client, 0, store([area(name="Chicago")])).status_code, 200)
        duplicate = store([area(name="Chicago"), area(name=" chicago ", area_id="area-2")])
        rejected = self.put(client, 1, duplicate)
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("unique", rejected.get_json()["error"])
        self.assertEqual(self.put(client, 1, store([area(name="Chicago"), area(name="Dallas", area_id="area-2")])).status_code, 200)

    def test_area_remark_fields_are_validated(self):
        client = self.client()
        bad_remark = area()
        bad_remark["remark"] = 5
        bad_radius = area()
        bad_radius["radius"] = {"text": 5}
        for value in (bad_remark, bad_radius):
            self.assertEqual(self.put(client, 0, store([value])).status_code, 400)
    def test_radius_choices_are_remembered_per_user(self):
        mine, other = self.client("admin"), self.client("panlijohn")
        self.assertEqual(mine.get("/api/truck-rate/preferences").get_json(), {"radius": {"last": None, "centers": {}}})
        self.assertEqual(mine.put("/api/truck-rate/preferences", json={"center": "pickup:o1", "radii": [60, 10, 30]}).status_code, 200)
        self.assertEqual(mine.put("/api/truck-rate/preferences", json={"center": "zip:IL|60106", "radii": [25, 50]}).status_code, 200)
        saved = mine.get("/api/truck-rate/preferences").get_json()["radius"]
        self.assertEqual(saved, {"last": [25, 50], "centers": {"pickup:o1": [10, 30, 60], "zip:IL|60106": [25, 50]}})
        self.assertEqual(other.get("/api/truck-rate/preferences").get_json()["radius"]["last"], None)
        self.assertEqual(other.put("/api/truck-rate/preferences", json={"center": None, "radii": [100]}).status_code, 200)
        self.assertEqual(mine.get("/api/truck-rate/preferences").get_json()["radius"]["last"], [25, 50])
        for bad in ({"radii": []}, {"radii": [0]}, {"radii": [251]}, {"radii": [12.5]}, {"radii": [10, 10]},
                    {"radii": [True]}, {"radii": list(range(1, 12))}, {"radii": [10], "center": "x"}, {"radii": "10"}):
            self.assertEqual(mine.put("/api/truck-rate/preferences", json=bad).status_code, 400, bad)

    def test_zips_within_rejects_bad_input(self):
        client = self.client()
        for query in ("", "lat=x&lng=1&miles=5", "lat=41&lng=-87&miles=0", "lat=41&lng=-87&miles=9999", "lat=95&lng=-87&miles=5"):
            self.assertEqual(client.get("/api/truck-rate/zips-within?" + query).status_code, 400, query)


if __name__ == "__main__":
    unittest.main()
