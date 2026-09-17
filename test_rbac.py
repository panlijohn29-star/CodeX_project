import importlib
import json
import os
import tempfile
import unittest


class RbacPlatformTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.auth_path = os.path.join(cls.temp_dir.name, "auth_users.json")
        cls.role_path = os.path.join(cls.temp_dir.name, "roles.json")
        with open(cls.auth_path, "w", encoding="utf-8") as handle:
            json.dump({"users": [
                {"user_id": "admin", "password": "admin-pass", "enabled": True, "favourites": [], "sql_access": True},
                {"user_id": "worker", "password": "worker-pass", "enabled": True, "favourites": ["closing_report"], "sql_access": True},
            ]}, handle)
        with open(cls.role_path, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": 1, "roles": []}, handle)
        os.environ["AUTH_CONFIG_PATH"] = cls.auth_path
        os.environ["ROLE_CONFIG_PATH"] = cls.role_path
        os.environ["FLASK_SECRET_KEY"] = "test-secret"
        os.environ["SESSION_COOKIE_SECURE"] = "true"
        import app_v4
        cls.platform = importlib.reload(app_v4)
        cls.platform.app.config["TESTING"] = True

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def login(self, client, user_id, password):
        return client.post("/login", data={"user_id": user_id, "password": password}, follow_redirects=False)

    def test_migration_hashes_passwords_and_defaults_non_admin_to_no_role(self):
        with open(self.auth_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertEqual(payload["schema_version"], 2)
        self.assertTrue(os.path.exists(self.auth_path + ".pre_rbac_backup.json"))
        worker = next(user for user in payload["users"] if user["user_id"] == "worker")
        self.assertNotIn("password", worker)
        self.assertTrue(worker["password_hash"])
        self.assertIsNone(worker["role_id"])

    def test_roles_control_feature_pages_apis_and_account_data(self):
        admin = self.platform.app.test_client()
        login_response = self.login(admin, "admin", "admin-pass")
        self.assertEqual(login_response.status_code, 302)
        self.assertIn("HttpOnly", login_response.headers["Set-Cookie"])
        self.assertIn("SameSite=Lax", login_response.headers["Set-Cookie"])
        self.assertIn("Secure", login_response.headers["Set-Cookie"])
        self.assertEqual(admin.get("/management/roles").status_code, 200)
        self.assertEqual(admin.get("/management/features").status_code, 200)

        create_role = admin.post("/api/roles", json={
            "id": "closing-user", "name": "Closing User", "remark": "Closing report only", "feature_ids": ["closing_report"],
        })
        self.assertEqual(create_role.status_code, 200)
        assign_role = admin.post("/api/account/role", json={"user_id": "worker", "role_id": "closing-user"})
        self.assertEqual(assign_role.status_code, 200)
        self.assertNotIn("password_hash", assign_role.get_json()["users"][0])

        worker = self.platform.app.test_client()
        self.assertEqual(self.login(worker, "worker", "worker-pass").status_code, 302)
        self.assertEqual(worker.get("/features/closing_report").status_code, 200)
        self.assertEqual(worker.get("/features/sql_query").status_code, 404)
        self.assertEqual(worker.get("/api/sql-query/scripts").status_code, 404)
        dashboard = worker.get("/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertNotIn(b"Account Management", dashboard.data)
        self.assertEqual(worker.get("/api/roles").status_code, 404)
        self.assertEqual(worker.post("/api/account", json={"user_id": "blocked", "password": "x"}).status_code, 404)

        self.assertEqual(admin.delete("/api/roles/closing-user").status_code, 400)
        self.assertEqual(admin.post("/api/account/role", json={"user_id": "worker", "role_id": None}).status_code, 200)
        self.assertEqual(worker.get("/features/closing_report").status_code, 404)
        self.assertEqual(worker.post("/api/runs", json={"feature_id": "closing_report", "inputs": {}}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
