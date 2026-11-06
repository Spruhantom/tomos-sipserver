import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from tomos_sipserver.api import create_app
from tomos_sipserver.db import connect, initialize_database


class ExtensionApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "extensions.db"
        initialize_database(self.db)
        self.applies = 0
        def apply():
            self.applies += 1
        self.client = TestClient(create_app(self.db, "test-token-" + "x" * 40, apply))
        self.headers = {"Authorization": "Bearer test-token-" + "x" * 40}

    def tearDown(self):
        self.tmp.cleanup()

    def test_health_is_public(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_extension_routes_require_token(self):
        self.assertEqual(self.client.get("/api/v1/extensions").status_code, 401)

    def test_create_list_update_disable_and_delete(self):
        created = self.client.post("/api/v1/extensions", headers=self.headers, json={"extension": "120", "display_name": "Office"})
        self.assertEqual(created.status_code, 201)
        self.assertEqual(len(created.json()["sip_password"]), 43)
        listed = self.client.get("/api/v1/extensions", headers=self.headers)
        self.assertEqual(listed.status_code, 200)
        self.assertNotIn("sip_password", listed.text)
        updated = self.client.patch("/api/v1/extensions/120", headers=self.headers, json={"display_name": "Study"})
        self.assertEqual(updated.json()["display_name"], "Study")
        disabled = self.client.post("/api/v1/extensions/120/disable", headers=self.headers)
        self.assertEqual(disabled.status_code, 200)
        self.assertFalse(disabled.json()["enabled"])
        self.assertGreaterEqual(self.applies, 3)
        deleted = self.client.delete("/api/v1/extensions/120", headers=self.headers)
        self.assertEqual(deleted.status_code, 204)

    def test_duplicate_extension_is_rejected(self):
        payload = {"extension": "121", "display_name": "Desk"}
        self.assertEqual(self.client.post("/api/v1/extensions", headers=self.headers, json=payload).status_code, 201)
        self.assertEqual(self.client.post("/api/v1/extensions", headers=self.headers, json=payload).status_code, 409)

    def test_failed_config_apply_rolls_back_database(self):
        def fail():
            raise RuntimeError("reload failed")
        client = TestClient(create_app(self.db, "another-test-token-" + "x" * 40, fail))
        response = client.post("/api/v1/extensions", headers={"Authorization": "Bearer another-test-token-" + "x" * 40}, json={"extension": "122", "display_name": "Rollback"})
        self.assertEqual(response.status_code, 503)
        with connect(self.db) as connection:
            self.assertIsNone(connection.execute("SELECT extension FROM extensions WHERE extension='122'").fetchone())

    def test_password_reset_returns_new_secret_once(self):
        self.client.post("/api/v1/extensions", headers=self.headers, json={"extension": "123", "display_name": "Lab"})
        reset = self.client.post("/api/v1/extensions/123/password", headers=self.headers)
        self.assertEqual(reset.status_code, 200)
        self.assertEqual(len(reset.json()["sip_password"]), 43)


if __name__ == "__main__":
    unittest.main()
