import importlib
import json
import os
import tempfile
import unittest
from unittest import mock

from werkzeug.security import generate_password_hash

import maintenance_notice


class MaintenanceNoticeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.notice_path = os.path.join(cls.temp_dir.name, "maintenance_notice.json")
        auth_path = os.path.join(cls.temp_dir.name, "auth_users.json")
        role_path = os.path.join(cls.temp_dir.name, "roles.json")

        def account(user_id, role_id=None):
            return {
                "user_id": user_id, "password_hash": generate_password_hash(user_id + "-pass"),
                "enabled": True, "favourites": [], "role_id": role_id,
            }

        with open(auth_path, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": 2, "users": [
                account("admin"), account("boss", "admin"), account("worker"),
            ]}, handle)
        with open(role_path, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": 1, "roles": []}, handle)
        cls.env_patch = mock.patch.dict(os.environ, {
            "AUTH_CONFIG_PATH": auth_path, "ROLE_CONFIG_PATH": role_path, "FLASK_SECRET_KEY": "test-secret",
        })
        cls.env_patch.start()
        cls.path_patch = mock.patch.object(maintenance_notice, "NOTICE_PATH", cls.notice_path)
        cls.path_patch.start()
        import app_v4
        cls.platform = importlib.reload(app_v4)
        cls.platform.app.config["TESTING"] = True

    @classmethod
    def tearDownClass(cls):
        cls.path_patch.stop()
        cls.env_patch.stop()
        # Reload with the original environment so later test modules do not inherit the deleted temp paths.
        with mock.patch.dict(os.environ, {"FLASK_SECRET_KEY": os.environ.get("FLASK_SECRET_KEY", "test-secret")}):
            importlib.reload(cls.platform)
        cls.temp_dir.cleanup()

    def setUp(self):
        self.clock = [1_800_000_000.0]
        patcher = mock.patch.object(maintenance_notice, "_now", lambda: self.clock[0])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(maintenance_notice.cancel)
        maintenance_notice.cancel()

    def client_for(self, user_id):
        client = self.platform.app.test_client()
        response = client.post("/login", data={"user_id": user_id, "password": user_id + "-pass"})
        self.assertEqual(response.status_code, 302)
        return client

    def schedule(self, client, seconds, message=""):
        return client.post("/api/maintenance", json={
            "restart_at": self.clock[0] + seconds, "message": message,
        })

    def test_only_admin_and_admin_role_can_schedule_or_cancel(self):
        worker = self.client_for("worker")
        self.assertEqual(self.schedule(worker, 600).status_code, 404)
        self.assertEqual(worker.delete("/api/maintenance").status_code, 404)
        for user_id in ("admin", "boss"):
            client = self.client_for(user_id)
            self.assertEqual(self.schedule(client, 600).status_code, 200)
            self.assertEqual(client.delete("/api/maintenance").status_code, 200)

    def test_every_signed_in_user_can_read_the_plan(self):
        self.assertEqual(self.schedule(self.client_for("admin"), 600, "Upgrade").status_code, 200)
        data = self.client_for("worker").get("/api/maintenance").get_json()
        self.assertFalse(data["can_manage"])
        self.assertEqual(data["plan"]["message"], "Upgrade")
        self.assertEqual(data["plan"]["phase"], "scheduled")
        self.assertEqual(data["server_time"], self.clock[0])
        self.assertEqual(self.platform.app.test_client().get("/api/maintenance").status_code, 401)

    def test_invalid_times_and_messages_are_rejected(self):
        admin = self.client_for("admin")
        self.assertEqual(self.schedule(admin, -60).status_code, 400)
        self.assertEqual(self.schedule(admin, 8 * 24 * 3600).status_code, 400)
        self.assertEqual(admin.post("/api/maintenance", json={"restart_at": "soon"}).status_code, 400)
        self.assertEqual(admin.post("/api/maintenance", json={}).status_code, 400)
        self.assertEqual(self.schedule(admin, 600, "x" * 301).status_code, 400)
        self.assertIsNone(admin.get("/api/maintenance").get_json()["plan"])

    def test_iso_time_with_offset_is_accepted(self):
        admin = self.client_for("admin")
        restart_at = maintenance_notice.datetime.fromtimestamp(
            self.clock[0] + 900, maintenance_notice.timezone.utc
        ).isoformat()
        response = admin.post("/api/maintenance", json={"restart_at": restart_at})
        self.assertEqual(response.status_code, 200)
        self.assertAlmostEqual(response.get_json()["plan"]["remaining_seconds"], 900, delta=1)

    def test_phases_follow_the_remaining_time(self):
        self.schedule(self.client_for("admin"), 600)
        worker = self.client_for("worker")
        expected = [(0, "scheduled"), (301, "warning"), (569, "warning"), (571, "countdown"), (599, "countdown"), (600, "maintenance")]
        for elapsed, phase in expected:
            self.clock[0] = 1_800_000_000.0 + elapsed
            if phase == "maintenance":
                worker = self.client_for("boss")
            self.assertEqual(worker.get("/api/maintenance").get_json()["plan"]["phase"], phase, elapsed)

    def test_new_runs_are_paused_during_the_last_five_minutes(self):
        admin = self.client_for("admin")
        self.schedule(admin, 600)
        self.assertNotEqual(admin.post("/api/runs", json={}).status_code, 503)
        self.clock[0] += 400
        for url in ("/api/runs", "/api/run", "/api/sql-query/runs"):
            response = admin.post(url, json={})
            self.assertEqual(response.status_code, 503, url)
            self.assertTrue(response.get_json()["maintenance"])
        self.assertEqual(admin.get("/api/runs").status_code, 200)

    def test_non_admin_sees_maintenance_page_after_restart_time(self):
        worker = self.client_for("worker")
        self.schedule(self.client_for("admin"), 600, "Back in 10 <b>min</b>")
        self.assertEqual(worker.get("/").status_code, 200)
        self.clock[0] += 601

        page = worker.get("/")
        self.assertEqual(page.status_code, 503)
        self.assertIn("System maintenance", page.get_data(as_text=True))
        self.assertIn("&lt;b&gt;min&lt;/b&gt;", page.get_data(as_text=True))
        api = worker.get("/api/features")
        self.assertEqual(api.status_code, 503)
        self.assertTrue(api.get_json()["maintenance"])
        self.assertEqual(worker.get("/api/maintenance").status_code, 503)
        self.assertEqual(self.platform.app.test_client().get("/maintenance-status").get_json(), {"maintenance": True})
        self.assertEqual(self.platform.app.test_client().get("/login").status_code, 200)

        for user_id in ("admin", "boss"):
            self.assertEqual(self.client_for(user_id).get("/").status_code, 200)

    def test_cancelling_lifts_maintenance_mode(self):
        worker = self.client_for("worker")
        admin = self.client_for("admin")
        self.schedule(admin, 600)
        self.clock[0] += 700
        self.assertEqual(worker.get("/").status_code, 503)
        self.assertEqual(admin.delete("/api/maintenance").status_code, 200)
        self.assertEqual(worker.get("/").status_code, 200)
        self.assertEqual(self.platform.app.test_client().get("/maintenance-status").get_json(), {"maintenance": False})

    def test_reminder_script_is_injected_once_for_signed_in_pages_only(self):
        worker = self.client_for("worker")
        page = worker.get("/").get_data(as_text=True)
        self.assertEqual(page.count("maintenance_notice.js"), 1)
        self.assertLess(page.index("maintenance_notice.js"), page.lower().rindex("</html>"))
        self.assertNotIn("maintenance_notice.js", self.platform.app.test_client().get("/login").get_data(as_text=True))
        self.assertEqual(worker.get("/static/maintenance_notice.js").status_code, 200)

    def test_dashboard_panel_is_admin_only(self):
        self.assertIn("maintenancePublishBtn", self.client_for("boss").get("/").get_data(as_text=True))
        self.assertNotIn("maintenancePublishBtn", self.client_for("worker").get("/").get_data(as_text=True))

    def test_restarting_the_process_clears_the_plan(self):
        self.schedule(self.client_for("admin"), 600)
        self.assertTrue(os.path.exists(self.notice_path))
        importlib.reload(self.platform)
        self.platform.app.config["TESTING"] = True
        self.assertFalse(os.path.exists(self.notice_path))


if __name__ == "__main__":
    unittest.main()
