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
        self.cdr_db = Path(self.tmp.name) / "cdr.db"
        import sqlite3
        with sqlite3.connect(self.cdr_db) as cdr:
            cdr.execute("CREATE TABLE cdr (calldate TEXT, clid TEXT, src TEXT, dst TEXT, duration REAL, billsec REAL, disposition TEXT)")
            cdr.executemany("INSERT INTO cdr VALUES (?, ?, ?, ?, ?, ?, ?)", [
                ("2026-11-06 20:00:00", "Office <100>", "100", "101", 35, 30, "ANSWERED"),
                ("2026-11-06 19:00:00", "Hall <101>", "101", "100", 12, 0, "NO ANSWER"),
            ])
        initialize_database(self.db)
        self.applies = 0
        def apply():
            self.applies += 1
        self.client = TestClient(create_app(self.db, "test-token-" + "x" * 40, apply, admin_password="admin-test-password-0123456789", cdr_database_path=self.cdr_db))
        self.headers = {"Authorization": "Bearer test-token-" + "x" * 40}

    def tearDown(self):
        self.tmp.cleanup()

    def test_health_is_public(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_call_history_is_authenticated_and_paginated(self):
        self.assertEqual(self.client.get("/api/v1/calls").status_code, 401)
        response = self.client.get("/api/v1/calls?limit=1&offset=1", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 2)
        self.assertEqual(response.json()["limit"], 1)
        self.assertEqual(response.json()["calls"][0]["source"], "101")
        self.assertEqual(response.json()["calls"][0]["disposition"], "NO ANSWER")
        self.assertNotIn("uniqueid", response.text)

    def test_call_history_limits_are_validated(self):
        self.assertEqual(self.client.get("/api/v1/calls?limit=101", headers=self.headers).status_code, 422)
        self.assertEqual(self.client.get("/api/v1/calls?offset=-1", headers=self.headers).status_code, 422)

    def test_call_history_returns_empty_when_cdr_database_not_created(self):
        token = "empty-cdr-token-" + "x" * 40
        client = TestClient(create_app(self.db, token, lambda: None,
                                       admin_password="admin-test-password-0123456789",
                                       cdr_database_path=Path(self.tmp.name) / "not-yet-created.db"))
        response = client.get("/api/v1/calls", headers={"Authorization": "Bearer " + token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["calls"], [])

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


    def test_dashboard_login_sets_session_and_csrf_protects_mutations(self):
        response = self.client.post("/api/v1/auth/login", json={"password": "admin-test-password-0123456789"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("tomos_session", response.cookies)
        self.assertEqual(self.client.get("/api/v1/extensions").status_code, 200)
        self.assertEqual(self.client.post("/api/v1/extensions", json={"extension": "124", "display_name": "No CSRF"}).status_code, 401)
        csrf = response.cookies.get("tomos_csrf")
        created = self.client.post("/api/v1/extensions", headers={"X-Tomos-CSRF": csrf}, json={"extension": "124", "display_name": "Browser"})
        self.assertEqual(created.status_code, 201)
        self.assertEqual(self.client.post("/api/v1/auth/logout").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/extensions").status_code, 401)

    def test_dashboard_status_returns_sanitised_device_snapshot(self):
        token = "status-token-" + "x" * 40
        app = create_app(self.db, token, lambda: None, admin_password="admin-test-password-0123456789",
                         status_callback=lambda: {"status":"online","uptime":"up 2 hours","registered_count":1,
                             "registered_contacts":[{"extension":"100","status":"Avail","uri":"sip:secret@10.0.0.2"}]})
        response = TestClient(app).get("/api/v1/dashboard/status", headers={"Authorization":"Bearer "+token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["registered_contacts"], [{"extension":"100","status":"Avail"}])
        self.assertNotIn("10.0.0.2", response.text)

    def test_dashboard_status_requires_authentication(self):
        self.assertEqual(self.client.get("/api/v1/dashboard/status").status_code, 401)

    def test_dashboard_rejects_wrong_password(self):
        response = self.client.post("/api/v1/auth/login", json={"password": "incorrect"})
        self.assertEqual(response.status_code, 401)

    def test_duplicate_extension_is_rejected(self):
        payload = {"extension": "121", "display_name": "Desk"}
        self.assertEqual(self.client.post("/api/v1/extensions", headers=self.headers, json=payload).status_code, 201)
        self.assertEqual(self.client.post("/api/v1/extensions", headers=self.headers, json=payload).status_code, 409)

    def test_failed_config_apply_rolls_back_database(self):
        def fail():
            raise RuntimeError("reload failed")
        client = TestClient(create_app(self.db, "another-test-token-" + "x" * 40, fail, admin_password="admin-test-password-0123456789"))
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
