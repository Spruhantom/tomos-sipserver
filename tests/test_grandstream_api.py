import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from fastapi.testclient import TestClient

from tomos_sipserver.api import create_app


class GrandstreamProvisioningApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "extensions.db"
        self.token = "grandstream-test-token-" + "x" * 40
        self.headers = {"Authorization": "Bearer " + self.token}
        self.client = TestClient(create_app(
            self.db, self.token, lambda: None,
            admin_password="admin-test-password-0123456789",
        ), base_url="https://testserver")
        created = self.client.post(
            "/api/v1/extensions", headers=self.headers,
            json={"extension": "126", "display_name": "Reception"},
        )
        self.assertEqual(created.status_code, 201)
        self.sip_password = created.json()["sip_password"]

    def tearDown(self):
        self.tmp.cleanup()

    def register(self, **overrides):
        payload = {
            "extension": "126", "mac": "00:0b:82:12:34:56",
            "model": "GRP2601", "server_host": "192.168.1.10",
        }
        payload.update(overrides)
        return self.client.post("/api/v1/devices/grandstream", headers=self.headers, json=payload)

    def test_register_and_download_mac_specific_xml(self):
        registered = self.register()
        self.assertEqual(registered.status_code, 201, registered.text)
        body = registered.json()
        self.assertEqual(body["provisioning_path"], "/provisioning/grandstream/cfg000b82123456.xml")
        self.assertTrue(body["password_displayed_once"])
        listed = self.client.get("/api/v1/devices/grandstream", headers=self.headers)
        self.assertEqual(listed.status_code, 200)
        self.assertNotIn(body["provisioning_password"], listed.text)
        response = self.client.get(
            body["provisioning_path"],
            auth=(body["provisioning_username"], body["provisioning_password"]),
        )
        self.assertEqual(response.status_code, 200, response.text)
        root = ET.fromstring(response.text)
        self.assertEqual(root.findtext("mac"), "000b82123456")
        values = {item.attrib["name"]: item.text for item in root.findall("./config/item")}
        self.assertEqual(values["account.1.sip.userid"], "126")
        self.assertEqual(values["account.1.sip.subscriber.userId"], "126")
        self.assertEqual(values["account.1.sip.subscriber.password"], self.sip_password)
        self.assertEqual(values["account.1.sip.server.1.address"], "192.168.1.10:5060")
        self.assertEqual(response.headers["cache-control"], "no-store, private")
        self.assertIn("application/xml", response.headers["content-type"])

    def test_authentication_and_https_are_required(self):
        registered = self.register().json()
        path = registered["provisioning_path"]
        self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.get(path, auth=(registered["provisioning_username"], "wrong")).status_code, 401)
        http_client = TestClient(self.client.app, base_url="http://testserver")
        response = http_client.get(path, auth=(registered["provisioning_username"], registered["provisioning_password"]))
        self.assertEqual(response.status_code, 400)

    def test_rejects_unknown_models_missing_or_disabled_extensions_and_duplicates(self):
        self.assertEqual(self.register(model="GRP2615").status_code, 422)
        self.assertEqual(self.register(extension="999").status_code, 404)
        self.assertEqual(self.register().status_code, 201)
        self.assertEqual(self.register().status_code, 409)
        self.client.post("/api/v1/extensions/126/disable", headers=self.headers)
        self.assertEqual(self.register(mac="00:0b:82:12:34:57").status_code, 409)

    def test_disabled_extension_and_revoked_credentials_block_download(self):
        registered = self.register().json()
        self.client.post("/api/v1/extensions/126/disable", headers=self.headers)
        disabled = self.client.get(
            registered["provisioning_path"],
            auth=(registered["provisioning_username"], registered["provisioning_password"]),
        )
        self.assertEqual(disabled.status_code, 410)
        self.client.post("/api/v1/extensions/126/enable", headers=self.headers)
        rotated = self.client.post(
            "/api/v1/devices/grandstream/000b82123456/credentials", headers=self.headers,
        ).json()
        old = self.client.get(
            registered["provisioning_path"],
            auth=(registered["provisioning_username"], registered["provisioning_password"]),
        )
        self.assertEqual(old.status_code, 401)
        new = self.client.get(
            registered["provisioning_path"],
            auth=(rotated["provisioning_username"], rotated["provisioning_password"]),
        )
        self.assertEqual(new.status_code, 200)
        deleted = self.client.delete("/api/v1/devices/grandstream/000b82123456", headers=self.headers)
        self.assertEqual(deleted.status_code, 204)
        missing = self.client.get(
            registered["provisioning_path"],
            auth=(rotated["provisioning_username"], rotated["provisioning_password"]),
        )
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
