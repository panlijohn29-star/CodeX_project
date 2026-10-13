import csv
from datetime import date
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import app_v4
import backup_truck_rate


class TruckRateBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_path = os.path.join(self.temp.name, "data", "truck_rate_data.json")
        path_patch = patch.object(app_v4, "TRUCK_RATE_DATA_PATH", self.data_path)
        path_patch.start()
        self.addCleanup(path_patch.stop)
        backup_patch = patch.dict(os.environ, {"TRUCK_RATE_BACKUP_DIR": os.path.join(self.temp.name, "backups")})
        backup_patch.start()
        self.addCleanup(backup_patch.stop)

    def save_store(self, store):
        os.makedirs(os.path.dirname(self.data_path), exist_ok=True)
        with open(self.data_path, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": 2, "revision": 1, "store": store, "history": []}, handle)

    def multi_pickup_store(self):
        def origin(origin_id, name, zone_set_id):
            return {"id": origin_id, "name": name, "street": "1 Main St.", "city": "X", "state": "IL",
                    "zip": "60106", "zoneSetId": zone_set_id}
        return {
            "origins": [origin("o1", "Bensenville", "z1"), origin("o2", "Aurora", "z1"), origin("o3", "Dallas", "z2")],
            "zoneSets": [{"id": "z1", "name": "Chicagoland"}, {"id": "z2", "name": "Texas"}],
            "areas": [
                {"id": "one", "zoneSetId": "z1", "name": 'Chicago, "North"', "group": "Midwest", "locations": [
                    {"id": "first", "city": "CHICAGO", "state": "IL", "zip": "60605"},
                    {"id": "second", "city": "AURORA", "state": "IL", "zip": "60502"},
                ]},
                {"id": "two", "zoneSetId": "z2", "name": "Dallas", "group": "South", "locations": []},
            ],
            "rates": [
                {"id": "r2", "originId": "o1", "areaId": "one", "ftlRate": "450", "perKiloRate": "0.9",
                 "effectiveFrom": "2026-10-01", "effectiveTo": ""},
                {"id": "r1", "originId": "o1", "areaId": "one", "ftlRate": "425", "perKiloRate": "0.85",
                 "effectiveFrom": "", "effectiveTo": "2026-09-30"},
                {"id": "r3", "originId": "o3", "areaId": "two", "ftlRate": "", "perKiloRate": "1.2",
                 "effectiveFrom": "", "effectiveTo": ""},
            ],
        }

    def test_daily_csv_matches_export_all_data_and_is_not_overwritten(self):
        self.save_store(self.multi_pickup_store())
        backup_path = backup_truck_rate.backup_truck_rate_csv(date(2026, 10, 8))
        self.assertEqual(os.path.basename(backup_path), "2026-10-08.csv")
        with open(backup_path, "r", encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(rows, [
            ["pickup", "zone_set", "area_name", "group", "ftl_rate", "per_kilo_rate", "effective_from", "effective_to",
             "city", "state", "zip"],
            ["Bensenville", "Chicagoland", 'Chicago, "North"', "Midwest", "425", "0.85", "", "2026-09-30", "CHICAGO", "IL", "60605"],
            ["Bensenville", "Chicagoland", 'Chicago, "North"', "Midwest", "425", "0.85", "", "2026-09-30", "AURORA", "IL", "60502"],
            ["Bensenville", "Chicagoland", 'Chicago, "North"', "Midwest", "450", "0.9", "2026-10-01", "", "CHICAGO", "IL", "60605"],
            ["Bensenville", "Chicagoland", 'Chicago, "North"', "Midwest", "450", "0.9", "2026-10-01", "", "AURORA", "IL", "60502"],
            ["Aurora", "Chicagoland", 'Chicago, "North"', "Midwest", "", "", "", "", "CHICAGO", "IL", "60605"],
            ["Aurora", "Chicagoland", 'Chicago, "North"', "Midwest", "", "", "", "", "AURORA", "IL", "60502"],
            ["Dallas", "Texas", "Dallas", "South", "", "1.2", "", "", "", "", ""],
        ])
        with open(backup_path, "rb") as handle:
            original = handle.read()
        self.save_store({"origins": [], "zoneSets": [], "areas": [], "rates": []})
        self.assertEqual(backup_truck_rate.backup_truck_rate_csv(date(2026, 10, 8)), backup_path)
        with open(backup_path, "rb") as handle:
            self.assertEqual(handle.read(), original)
        self.assertTrue(backup_truck_rate.backup_truck_rate_csv(date(2026, 10, 9)).endswith("2026-10-09.csv"))
        os.remove(self.data_path)
        self.assertIsNone(backup_truck_rate.backup_truck_rate_csv(date(2026, 10, 8)))

    def test_single_pickup_data_is_backed_up_without_rewriting_the_data_file(self):
        self.save_store({"areas": [
            {"id": "one", "name": "IL - ZONE 1", "group": "IL", "ftlRate": "", "perKiloRate": "0.2",
             "locations": [{"id": "first", "city": "CHICAGO", "state": "IL", "zip": "60605"}]},
        ]})
        with open(self.data_path, "rb") as handle:
            before = handle.read()
        with open(backup_truck_rate.backup_truck_rate_csv(date(2026, 10, 8)), "r", encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(rows[1], ["Bensenville", "Bensenville", "IL - ZONE 1", "IL", "", "0.2", "", "", "CHICAGO", "IL", "60605"])
        with open(self.data_path, "rb") as handle:
            self.assertEqual(handle.read(), before)

    def test_missing_or_invalid_data_does_not_create_backup(self):
        self.assertIsNone(backup_truck_rate.backup_truck_rate_csv(date(2026, 10, 8)))
        self.assertFalse(os.path.exists(os.environ["TRUCK_RATE_BACKUP_DIR"]))
        os.makedirs(os.path.dirname(self.data_path), exist_ok=True)
        with open(self.data_path, "w", encoding="utf-8") as handle:
            handle.write("invalid JSON")
        with self.assertRaises(ValueError):
            backup_truck_rate.backup_truck_rate_csv(date(2026, 10, 8))
        self.assertFalse(os.path.exists(os.environ["TRUCK_RATE_BACKUP_DIR"]))


if __name__ == "__main__":
    unittest.main()
