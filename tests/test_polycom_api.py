import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from fastapi.testclient import TestClient

from tomos_sipserver.api import create_app


class PolycomProvisioningApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "extensions.db"
        self.token = "polycom-test-token-" + "x" * 40
        self.headers = {"Authorization": "Bearer " + self.token}
        self.client = TestClient(create_app(
            self.db, self.token, lambda: None,
            admin_password="admin-test-password-0123456789",
        ), base_url="https://testserver")
        created = self.client.post(
            "/api/v1/extensions", headers=self.headers,
            json={"extension": "127", "display_name": "Front Desk"},
        )
        self.assertEqual(created.status_code, 201)
        self.sip_password = created.json()["sip_password"]

    def tearDown(self):
        self.tmp.cleanup()

    def register(self, **overrides):
        payload = {
            "extension": "127", "mac": "00:04:f2:12:34:56",
            "model": "VVX450", "server_host": "192.168.1.10",
            "server_port": 5061, "transport": "TLS",
        }
        payload.update(overrides)
        return self.client.post("/api/v1/devices/polycom", headers=self.headers, json=payload)

    def test_register_and_download_mac_specific_xml_cfg(self):
        registered = self.register()
        self.assertEqual(registered.status_code, 201, registered.text)
        body = registered.json()
        self.assertEqual(body["provisioning_path"], "/provisioning/polycom/0004f2123456.cfg")
        self.assertTrue(body["password_displayed_once"])
        listed = self.client.get("/api/v1/devices/polycom", headers=self.headers)
        self.assertEqual(listed.status_code, 200)
        self.assertNotIn(body["provisioning_password"], listed.text)
        response = self.client.get(
            body["provisioning_path"],
            auth=(body["provisioning_username"], body["provisioning_password"]),
        )
        self.assertEqual(response.status_code, 200, response.text)
        reg = ET.fromstring(response.text).find("reg")
        self.assertEqual(reg.attrib["reg.1.address"], "127")
        self.assertEqual(reg.attrib["reg.1.auth.userId"], "127")
        self.assertEqual(reg.attrib["reg.1.auth.password"], self.sip_password)
        self.assertEqual(reg.attrib["reg.1.server.1.address"], "192.168.1.10")
        self.assertEqual(reg.attrib["reg.1.server.1.port"], "5061")
        self.assertEqual(reg.attrib["reg.1.server.1.transport"], "TLS")
        self.assertEqual(response.headers["cache-control"], "no-store, private")

    def test_https_and_basic_auth_are_required(self):
        registered = self.register().json()
        path = registered["provisioning_path"]
        self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.get(path, auth=(registered["provisioning_username"], "wrong")).status_code, 401)
        http_client = TestClient(self.client.app, base_url="http://testserver")
        response = http_client.get(path, auth=(registered["provisioning_username"], registered["provisioning_password"]))
        self.assertEqual(response.status_code, 400)

    def test_model_transport_extension_and_duplicate_validation(self):
        self.assertEqual(self.register(model="CCX350").status_code, 422)
        self.assertEqual(self.register(transport="SCTP").status_code, 422)
        self.assertEqual(self.register(extension="999").status_code, 404)
        self.assertEqual(self.register().status_code, 201)
        self.assertEqual(self.register().status_code, 409)
        self.client.post("/api/v1/extensions/127/disable", headers=self.headers)
        self.assertEqual(self.register(mac="00:04:f2:12:34:57").status_code, 409)

    def test_rotate_and_revoke_device_credentials(self):
        registered = self.register().json()
        rotated = self.client.post(
            "/api/v1/devices/polycom/0004f2123456/credentials", headers=self.headers,
        )
        self.assertEqual(rotated.status_code, 200, rotated.text)
        replacement = rotated.json()
        old = self.client.get(
            registered["provisioning_path"],
            auth=(registered["provisioning_username"], registered["provisioning_password"]),
        )
        self.assertEqual(old.status_code, 401)
        new = self.client.get(
            registered["provisioning_path"],
            auth=(replacement["provisioning_username"], replacement["provisioning_password"]),
        )
        self.assertEqual(new.status_code, 200)
        deleted = self.client.delete("/api/v1/devices/polycom/0004f2123456", headers=self.headers)
        self.assertEqual(deleted.status_code, 204)
        missing = self.client.get(
            registered["provisioning_path"],
            auth=(replacement["provisioning_username"], replacement["provisioning_password"]),
        )
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
