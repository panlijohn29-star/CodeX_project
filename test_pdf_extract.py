import importlib
import json
import os
import tempfile
import unittest


class PdfExtractIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        auth_path = os.path.join(cls.temp_dir.name, "auth_users.json")
        role_path = os.path.join(cls.temp_dir.name, "roles.json")
        with open(auth_path, "w", encoding="utf-8") as handle:
            json.dump({"users": [
                {"user_id": "admin", "password": "admin-pass", "enabled": True, "favourites": []},
                {"user_id": "worker", "password": "worker-pass", "enabled": True, "favourites": []},
            ]}, handle)
        with open(role_path, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": 1, "roles": []}, handle)
        os.environ["AUTH_CONFIG_PATH"] = auth_path
        os.environ["ROLE_CONFIG_PATH"] = role_path
        os.environ["PDF_EXTRACT_DATA_DIR"] = os.path.join(cls.temp_dir.name, "pdf_extract")
        os.environ["FLASK_SECRET_KEY"] = "test-secret"
        import app_v4
        cls.platform = importlib.reload(app_v4)
        cls.platform.app.config["TESTING"] = True

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def login(self, user_id, password):
        client = self.platform.app.test_client()
        client.post("/login", data={"user_id": user_id, "password": password})
        return client

    def test_requires_login(self):
        client = self.platform.app.test_client()
        self.assertEqual(client.get("/tools/pdf-extract/").status_code, 302)
        self.assertEqual(client.get("/tools/pdf-extract/api/templates").status_code, 302)

    def test_user_without_grant_gets_404(self):
        worker = self.login("worker", "worker-pass")
        self.assertEqual(worker.get("/tools/pdf-extract/").status_code, 404)
        self.assertEqual(worker.get("/tools/pdf-extract/api/templates").status_code, 404)
        self.assertEqual(worker.get("/features/pdf_extract").status_code, 404)

    def test_admin_page_assets_and_api_under_prefix(self):
        admin = self.login("admin", "admin-pass")
        self.assertEqual(admin.get("/features/pdf_extract").status_code, 200)

        page = admin.get("/tools/pdf-extract/")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn('window.__PDF_EXTRACT_BASE="/tools/pdf-extract"', html)
        self.assertIn('"/tools/pdf-extract/static/app.js', html)

        self.assertEqual(admin.get("/tools/pdf-extract/static/app.js").status_code, 200)
        templates = admin.get("/tools/pdf-extract/api/templates")
        self.assertEqual(templates.status_code, 200)
        self.assertEqual(templates.get_json(), [])
        self.assertEqual(admin.get("/tools/pdf-extract/api/documents").status_code, 200)

        created = admin.post("/tools/pdf-extract/api/templates", json={"name": "Vendor A", "keywords": ["vendor a"]})
        self.assertEqual(created.status_code, 200)
        self.assertEqual(len(admin.get("/tools/pdf-extract/api/templates").get_json()), 1)

        self.assertEqual(admin.get("/tools/pdf-extract/api/documents/999/file").status_code, 404)

    def test_data_lives_in_configured_directory(self):
        self.assertTrue(os.path.exists(os.path.join(self.temp_dir.name, "pdf_extract", "app.db")))


if __name__ == "__main__":
    unittest.main()
