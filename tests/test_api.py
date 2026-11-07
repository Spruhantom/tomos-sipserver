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

    def test_call_history_filters_by_date_number_and_disposition(self):
        response = self.client.get("/api/v1/calls?start_date=2026-11-06&end_date=2026-11-06&number=101&disposition=answered", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 1)
        self.assertEqual(response.json()["calls"][0]["destination"], "101")

    def test_call_history_rejects_invalid_date_range_and_disposition(self):
        self.assertEqual(self.client.get("/api/v1/calls?start_date=2026-02-30", headers=self.headers).status_code, 422)
        self.assertEqual(self.client.get("/api/v1/calls?start_date=2026-11-07&end_date=2026-11-06", headers=self.headers).status_code, 422)
        self.assertEqual(self.client.get("/api/v1/calls?disposition=anything", headers=self.headers).status_code, 422)

    def test_call_history_csv_export_is_authenticated_and_formula_safe(self):
        self.assertEqual(self.client.get("/api/v1/calls/export.csv").status_code, 401)
        import sqlite3
        with sqlite3.connect(self.cdr_db) as cdr:
            cdr.execute("UPDATE cdr SET clid = ? WHERE src = '100'", ("=HYPERLINK(\"https://bad.example\")",))
        response = self.client.get("/api/v1/calls/export.csv?number=100", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.headers["content-type"])
        self.assertIn("'=HYPERLINK", response.text)
        self.assertIn("attachment;", response.headers["content-disposition"])

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
        response = self.client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin-test-password-0123456789"})
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

    def test_dashboard_login_requires_correct_username(self):
        response = self.client.post("/api/v1/auth/login", json={
            "username": "not-admin", "password": "admin-test-password-0123456789"
        })
        self.assertEqual(response.status_code, 401)

    def test_dashboard_login_rejects_invalid_username_format(self):
        response = self.client.post("/api/v1/auth/login", json={
            "username": "x", "password": "admin-test-password-0123456789"
        })
        self.assertEqual(response.status_code, 422)

    def test_dashboard_rejects_wrong_password(self):
        response = self.client.post("/api/v1/auth/login", json={"username": "admin", "password": "incorrect"})
        self.assertEqual(response.status_code, 401)

    def test_dashboard_login_rate_limits_repeated_failures_and_clears_after_success(self):
        for _ in range(4):
            response = self.client.post("/api/v1/auth/login", json={"username": "admin", "password": "incorrect"})
            self.assertEqual(response.status_code, 401)
        accepted = self.client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin-test-password-0123456789"})
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(self.client.post("/api/v1/auth/logout").status_code, 200)
        for _ in range(5):
            response = self.client.post("/api/v1/auth/login", json={"username": "admin", "password": "incorrect"})
            self.assertEqual(response.status_code, 401)
        limited = self.client.post("/api/v1/auth/login", json={"username": "admin", "password": "incorrect"})
        self.assertEqual(limited.status_code, 429)
        self.assertIn("Retry-After", limited.headers)

    def test_device_provisioning_returns_safe_sip_settings_without_password(self):
        created = self.client.post("/api/v1/extensions", headers=self.headers,
                                   json={"extension": "125", "display_name": "Workshop"})
        self.assertEqual(created.status_code, 201)
        self.assertEqual(self.client.get("/api/v1/extensions/125/provisioning").status_code, 401)
        response = self.client.get("/api/v1/extensions/125/provisioning", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        profile = response.json()
        self.assertEqual(profile["sip_username"], "125")
        self.assertEqual(profile["authentication_username"], "125")
        self.assertEqual(profile["server_port"], 5060)
        self.assertEqual(profile["transport"], "UDP")
        self.assertEqual(profile["codecs"], ["alaw", "ulaw"])
        self.assertFalse(profile["password_included"])
        self.assertNotIn("password", profile)
        self.assertNotIn("sip_password", profile)
        disabled = self.client.post("/api/v1/extensions/125/disable", headers=self.headers)
        self.assertEqual(disabled.status_code, 200)
        self.assertFalse(self.client.get("/api/v1/extensions/125/provisioning", headers=self.headers).json()["enabled"])
        self.assertEqual(self.client.get("/api/v1/extensions/999/provisioning", headers=self.headers).status_code, 404)
        self.assertEqual(self.client.get("/api/v1/extensions/12/provisioning", headers=self.headers).status_code, 422)

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

    def test_dashboard_passwords_are_hashed_in_database(self):
        with connect(self.db) as connection:
            row = connection.execute("SELECT username, role, password_hash FROM admin_users").fetchone()
        self.assertEqual(row["username"], "admin")
        self.assertEqual(row["role"], "admin")
        self.assertTrue(row["password_hash"].startswith("pbkdf2_sha256$"))
        self.assertNotEqual(row["password_hash"], "admin-test-password-0123456789")

    def test_admin_can_create_and_list_operator_and_viewer_accounts(self):
        login = self.client.post("/api/v1/auth/login", json={"username":"admin","password":"admin-test-password-0123456789"})
        csrf = login.cookies.get("tomos_csrf")
        headers = {"X-Tomos-CSRF": csrf}
        operator = self.client.post("/api/v1/users", headers=headers, json={"username":"operator1","password":"strong-password-123","role":"operator"})
        self.assertEqual(operator.status_code, 201, operator.text)
        self.assertEqual(operator.json()["role"], "operator")
        viewer = self.client.post("/api/v1/users", headers=headers, json={"username":"viewer1","password":"strong-password-456","role":"viewer"})
        self.assertEqual(viewer.status_code, 201, viewer.text)
        listed = self.client.get("/api/v1/users")
        self.assertEqual(listed.status_code, 200)
        self.assertNotIn("password_hash", listed.text)

    def test_viewer_is_read_only_and_cannot_manage_users(self):
        login = self.client.post("/api/v1/auth/login", json={"username":"admin","password":"admin-test-password-0123456789"})
        self.client.post("/api/v1/users", headers={"X-Tomos-CSRF":login.cookies.get("tomos_csrf")},
                         json={"username":"viewer2","password":"strong-password-789","role":"viewer"})
        viewer = TestClient(self.client.app)
        viewer_login = viewer.post("/api/v1/auth/login", json={"username":"viewer2","password":"strong-password-789"})
        self.assertEqual(viewer_login.status_code, 200)
        self.assertEqual(viewer.get("/api/v1/extensions").status_code, 200)
        self.assertEqual(viewer.post("/api/v1/extensions", headers={"X-Tomos-CSRF":viewer_login.cookies.get("tomos_csrf")}, json={"extension":"133","display_name":"Denied"}).status_code, 403)
        self.assertEqual(viewer.get("/api/v1/users").status_code, 403)

    def test_operator_can_manage_extensions_but_not_dashboard_users(self):
        login = self.client.post("/api/v1/auth/login", json={"username":"admin","password":"admin-test-password-0123456789"})
        self.client.post("/api/v1/users", headers={"X-Tomos-CSRF":login.cookies.get("tomos_csrf")},
                         json={"username":"operator2","password":"strong-password-abc","role":"operator"})
        operator = TestClient(self.client.app)
        operator_login = operator.post("/api/v1/auth/login", json={"username":"operator2","password":"strong-password-abc"})
        self.assertEqual(operator_login.status_code, 200)
        self.assertEqual(operator.post("/api/v1/extensions", headers={"X-Tomos-CSRF":operator_login.cookies.get("tomos_csrf")}, json={"extension":"134","display_name":"Operator desk"}).status_code, 201)
        self.assertEqual(operator.get("/api/v1/users").status_code, 403)

    def test_last_active_admin_cannot_be_demoted_disabled_or_deleted(self):
        login = self.client.post("/api/v1/auth/login", json={"username":"admin","password":"admin-test-password-0123456789"})
        csrf = login.cookies.get("tomos_csrf")
        admin_id = self.client.get("/api/v1/users").json()[0]["id"]
        headers = {"X-Tomos-CSRF":csrf}
        self.assertEqual(self.client.patch(f"/api/v1/users/{admin_id}", headers=headers, json={"role":"operator"}).status_code, 409)
        self.assertEqual(self.client.patch(f"/api/v1/users/{admin_id}", headers=headers, json={"enabled":False}).status_code, 409)
        self.assertEqual(self.client.delete(f"/api/v1/users/{admin_id}", headers=headers).status_code, 409)

    def test_disabled_account_cannot_log_in_and_admin_can_reset_password(self):
        login = self.client.post("/api/v1/auth/login", json={"username":"admin","password":"admin-test-password-0123456789"})
        csrf = login.cookies.get("tomos_csrf")
        created = self.client.post("/api/v1/users", headers={"X-Tomos-CSRF":csrf},
                                   json={"username":"operator3","password":"strong-password-xyz","role":"operator"})
        user_id = created.json()["id"]
        self.assertEqual(self.client.patch(f"/api/v1/users/{user_id}", headers={"X-Tomos-CSRF":csrf}, json={"enabled":False}).status_code, 200)
        disabled_client = TestClient(self.client.app)
        self.assertEqual(disabled_client.post("/api/v1/auth/login", json={"username":"operator3","password":"strong-password-xyz"}).status_code, 401)
        self.assertEqual(self.client.post(f"/api/v1/users/{user_id}/password", headers={"X-Tomos-CSRF":csrf},
                                          json={"password":"new-strong-password-123"}).status_code, 200)

    def test_user_can_change_own_password(self):
        login = self.client.post("/api/v1/auth/login", json={"username":"admin","password":"admin-test-password-0123456789"})
        self.assertEqual(login.status_code, 200)
        changed = self.client.post("/api/v1/auth/password", headers={"X-Tomos-CSRF":login.cookies.get("tomos_csrf")}, json={"old_password":"admin-test-password-0123456789","new_password":"replacement-admin-password-123"})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(self.client.post("/api/v1/auth/logout").status_code, 200)
        relogin = self.client.post("/api/v1/auth/login", json={"username":"admin","password":"replacement-admin-password-123"})
        self.assertEqual(relogin.status_code, 200)

if __name__ == "__main__":
    unittest.main()
