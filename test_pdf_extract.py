import importlib
import json
import os
import tempfile
import unittest
from unittest import mock

from werkzeug.security import generate_password_hash


class PdfExtractIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        auth_path = os.path.join(cls.temp_dir.name, "auth_users.json")
        role_path = os.path.join(cls.temp_dir.name, "roles.json")
        with open(auth_path, "w", encoding="utf-8") as handle:
            def account(user_id, password, role_id=None):
                return {"user_id": user_id, "password_hash": generate_password_hash(password), "enabled": True,
                        "favourites": [], "role_id": role_id}

            json.dump({"schema_version": 2, "users": [
                account("admin", "admin-pass"),
                account("worker", "worker-pass"),
                account("boss", "boss-pass", "admin"),
                account("granted", "granted-pass", "pdf-role"),
            ]}, handle)
        with open(role_path, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": 1, "roles": [
                {"id": "pdf-role", "name": "PDF Role", "remark": "", "feature_ids": ["pdf_extract"]},
            ]}, handle)
        cls.env_patch = mock.patch.dict(os.environ, {
            "AUTH_CONFIG_PATH": auth_path, "ROLE_CONFIG_PATH": role_path,
            "PDF_EXTRACT_DATA_DIR": os.path.join(cls.temp_dir.name, "pdf_extract"), "FLASK_SECRET_KEY": "test-secret",
        })
        cls.env_patch.start()
        import app_v4
        cls.platform = importlib.reload(app_v4)
        cls.platform.app.config["TESTING"] = True

    @classmethod
    def tearDownClass(cls):
        cls.env_patch.stop()
        with mock.patch.dict(os.environ, {"FLASK_SECRET_KEY": os.environ.get("FLASK_SECRET_KEY", "test-secret")}):
            importlib.reload(cls.platform)
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

    def test_granted_role_opens_tool_but_settings_are_admin_only(self):
        granted = self.login("granted", "granted-pass")
        page = granted.get("/tools/pdf-extract/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("window.__PDF_EXTRACT_ADMIN=false", page.get_data(as_text=True))
        self.assertEqual(granted.get("/tools/pdf-extract/api/templates").status_code, 200)

        shown = granted.get("/tools/pdf-extract/api/settings").get_json()
        self.assertEqual(list(shown["settings"]), ["claude_model"])
        self.assertEqual(shown["providers"], {})
        self.assertTrue(shown["field_types"])
        self.assertEqual(granted.put("/tools/pdf-extract/api/settings", json={"own_companies": "X"}).status_code, 403)
        # A forged header from the browser must not unlock anything.
        forged = {"X-Platform-Admin": "1"}
        self.assertEqual(granted.put("/tools/pdf-extract/api/settings", json={"own_companies": "X"}, headers=forged).status_code, 403)
        self.assertNotIn("custom_api_key", granted.get("/tools/pdf-extract/api/settings", headers=forged).get_json()["settings"])

    def test_admin_and_admin_role_see_and_change_settings(self):
        for user_id, password in (("admin", "admin-pass"), ("boss", "boss-pass")):
            client = self.login(user_id, password)
            self.assertIn("window.__PDF_EXTRACT_ADMIN=true", client.get("/tools/pdf-extract/").get_data(as_text=True))
            self.assertIn("custom_api_key", client.get("/tools/pdf-extract/api/settings").get_json()["settings"])
            self.assertEqual(client.put("/tools/pdf-extract/api/settings", json={"own_companies": user_id}).status_code, 200)

    def test_model_list_is_admin_only_and_reports_provider_errors(self):
        from unittest import mock

        from pdf_extract.backend import llm

        models = [{"id": "claude-sonnet-5-5", "name": "Claude Sonnet 5.5"}]
        with mock.patch.object(llm, "list_models", return_value=models):
            granted = self.login("granted", "granted-pass")
            self.assertEqual(granted.get("/tools/pdf-extract/api/models/claude").status_code, 403)
            admin = self.login("admin", "admin-pass")
            self.assertEqual(admin.get("/tools/pdf-extract/api/models/claude").get_json(), {"models": models})
            self.assertEqual(admin.get("/tools/pdf-extract/api/models/other").status_code, 404)
        failing = mock.patch.object(llm, "list_models", side_effect=llm.LLMError("no key"))
        with failing:
            self.assertEqual(admin.get("/tools/pdf-extract/api/models/deepseek").status_code, 502)

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
