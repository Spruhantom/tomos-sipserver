import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from tomos_sipserver.api import create_app


class YealinkProvisioningApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "extensions.db"
        self.token = "yealink-test-token-" + "x" * 40
        self.headers = {"Authorization": "Bearer " + self.token}
        self.client = TestClient(create_app(
            self.db, self.token, lambda: None,
            admin_password="admin-test-password-0123456789",
        ), base_url="https://testserver")
        created = self.client.post(
            "/api/v1/extensions", headers=self.headers,
            json={"extension": "125", "display_name": "Workshop"},
        )
        self.assertEqual(created.status_code, 201)
        self.sip_password = created.json()["sip_password"]

    def tearDown(self):
        self.tmp.cleanup()

    def register(self, **overrides):
        payload = {
            "extension": "125", "mac": "00:15:65:aa:bb:cc",
            "model": "SIP-T46U", "server_host": "192.168.1.10",
        }
        payload.update(overrides)
        return self.client.post("/api/v1/devices/yealink", headers=self.headers, json=payload)

    def test_register_device_issues_one_time_basic_credentials(self):
        response = self.register()
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["mac"], "001565AABBCC")
        self.assertEqual(body["extension"], "125")
        self.assertEqual(body["provisioning_path"], "/provisioning/yealink/001565AABBCC.cfg")
        self.assertTrue(body["password_displayed_once"])
        self.assertNotIn("provisioning_password_hash", body)
        listed = self.client.get("/api/v1/devices/yealink", headers=self.headers)
        self.assertEqual(listed.status_code, 200)
        self.assertNotIn(body["provisioning_password"], listed.text)

    def test_cfg_requires_https_and_device_basic_auth(self):
        registered = self.register().json()
        path = registered["provisioning_path"]
        no_auth = self.client.get(path)
        self.assertEqual(no_auth.status_code, 401)
        wrong = self.client.get(path, auth=(registered["provisioning_username"], "wrong"))
        self.assertEqual(wrong.status_code, 401)
        valid = self.client.get(path, auth=(
            registered["provisioning_username"], registered["provisioning_password"]
        ))
        self.assertEqual(valid.status_code, 200, valid.text)
        self.assertIn("account.1.user_name = 125", valid.text)
        self.assertIn("account.1.password = " + self.sip_password, valid.text)
        self.assertIn("account.1.sip_server.1.address = 192.168.1.10", valid.text)
        self.assertEqual(valid.headers["cache-control"], "no-store, private")

    def test_http_is_rejected_even_with_valid_device_credentials(self):
        registered = self.register().json()
        http_client = TestClient(self.client.app, base_url="http://testserver")
        response = http_client.get(
            registered["provisioning_path"],
            auth=(registered["provisioning_username"], registered["provisioning_password"]),
        )
        self.assertEqual(response.status_code, 400)

    def test_invalid_or_duplicate_devices_are_rejected(self):
        self.assertEqual(self.register(mac="not-a-mac").status_code, 422)
        self.assertEqual(self.register(server_host="https://pbx.home").status_code, 422)
        self.assertEqual(self.register().status_code, 201)
        self.assertEqual(self.register().status_code, 409)

    def test_missing_or_disabled_extension_cannot_be_provisioned(self):
        self.assertEqual(self.register(extension="999").status_code, 404)
        self.client.post("/api/v1/extensions/125/disable", headers=self.headers)
        self.assertEqual(self.register(mac="00:15:65:aa:bb:cd").status_code, 409)

    def test_disabled_extension_stops_existing_device_profile(self):
        registered = self.register().json()
        self.client.post("/api/v1/extensions/125/disable", headers=self.headers)
        response = self.client.get(
            registered["provisioning_path"],
            auth=(registered["provisioning_username"], registered["provisioning_password"]),
        )
        self.assertEqual(response.status_code, 410)

    def test_device_delete_revokes_provisioning_access(self):
        registered = self.register().json()
        deleted = self.client.delete(
            "/api/v1/devices/yealink/00:15:65:aa:bb:cc", headers=self.headers
        )
        self.assertEqual(deleted.status_code, 204)
        response = self.client.get(
            registered["provisioning_path"],
            auth=(registered["provisioning_username"], registered["provisioning_password"]),
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
