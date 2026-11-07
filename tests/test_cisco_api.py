import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from tomos_sipserver.api import create_app


class CiscoInventoryApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "extensions.db"
        self.token = "cisco-test-token-" + "x" * 40
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

    def tearDown(self):
        self.tmp.cleanup()

    def register(self, **overrides):
        payload = {
            "extension": "125", "mac": "00:15:65:aa:bb:cc",
            "model": "7940", "server_host": "192.168.1.10",
        }
        payload.update(overrides)
        return self.client.post("/api/v1/devices/cisco", headers=self.headers, json=payload)

    def test_register_and_list_cisco_handset_inventory(self):
        response = self.register(model="Cisco 7960")
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["mac"], "001565AABBCC")
        self.assertEqual(body["model"], "7960")
        self.assertEqual(body["sccp_config_status"], "generated")
        self.assertEqual(body["tftp_profile_status"], "not_configured")
        self.assertEqual(body["hardware_test_status"], "not_tested")
        listed = self.client.get("/api/v1/devices/cisco", headers=self.headers)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)
        self.assertEqual(listed.json()[0]["extension"], "125")
        self.assertEqual(listed.json()[0]["sccp_config_status"], "generated")
        self.assertEqual(listed.json()[0]["tftp_profile_status"], "not_configured")
        self.assertEqual(listed.json()[0]["hardware_test_status"], "not_tested")

    def test_only_initial_target_models_are_accepted(self):
        for model in ("7940", "7960", "7970"):
            response = self.register(mac=f"00:15:65:aa:bb:{int(model[-2:]):02x}", model=model)
            self.assertEqual(response.status_code, 201, response.text)
        rejected = self.register(mac="00:15:65:aa:bb:dd", model="8841")
        self.assertEqual(rejected.status_code, 422)

    def test_invalid_mac_or_host_is_rejected(self):
        self.assertEqual(self.register(mac="not-a-mac").status_code, 422)
        self.assertEqual(self.register(server_host="https://pbx.home").status_code, 422)

    def test_duplicate_mac_is_rejected(self):
        self.assertEqual(self.register().status_code, 201)
        self.assertEqual(self.register().status_code, 409)

    def test_missing_or_disabled_extension_cannot_be_assigned(self):
        self.assertEqual(self.register(extension="999").status_code, 404)
        self.client.post("/api/v1/extensions/125/disable", headers=self.headers)
        self.assertEqual(self.register(mac="00:15:65:aa:bb:cd").status_code, 409)

    def test_delete_removes_inventory_record(self):
        self.assertEqual(self.register().status_code, 201)
        deleted = self.client.delete(
            "/api/v1/devices/cisco/00:15:65:aa:bb:cc", headers=self.headers
        )
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(self.client.get("/api/v1/devices/cisco", headers=self.headers).json(), [])

    def test_inventory_requires_admin_authentication(self):
        self.assertEqual(self.client.get("/api/v1/devices/cisco").status_code, 401)


if __name__ == "__main__":
    unittest.main()
