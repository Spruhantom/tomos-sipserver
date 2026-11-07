import unittest
import xml.etree.ElementTree as ET

from tomos_sipserver.grandstream import normalize_model, render_grandstream_xml


class GrandstreamRendererTests(unittest.TestCase):
    def test_model_allowlist(self):
        self.assertEqual(normalize_model("grp2601"), "GRP2601")
        with self.assertRaises(ValueError):
            normalize_model("GRP2615")

    def test_xml_contains_account_settings_and_mac(self):
        xml = render_grandstream_xml(
            mac="00:0b:82:12:34:56", extension="120", display_name="Office",
            sip_password="secret&safe", server_host="pbx.example",
        )
        root = ET.fromstring(xml)
        self.assertEqual(root.findtext("mac"), "000b82123456")
        items = {item.attrib["name"]: item.text for item in root.findall("./config/item")}
        self.assertEqual(items["account.1.enable"], "Yes")
        self.assertEqual(items["account.1.sip.server.1.address"], "pbx.example:5060")
        self.assertEqual(items["account.1.sip.userid"], "120")
        self.assertEqual(items["account.1.sip.subscriber.userId"], "120")
        self.assertEqual(items["account.1.sip.subscriber.password"], "secret&safe")
        self.assertEqual(items["account.1.sip.subscriber.name"], "Office")

    def test_xml_escapes_untrusted_values(self):
        xml = render_grandstream_xml(
            mac="000b82123456", extension="120", display_name="Office & Desk",
            sip_password="a<b&c", server_host="pbx.example",
        )
        root = ET.fromstring(xml)
        self.assertEqual(root.findtext("./config/item[@name='account.1.name']"), "Office & Desk")
        self.assertEqual(root.findtext("./config/item[@name='account.1.sip.subscriber.password']"), "a<b&c")

    def test_rejects_invalid_values_and_control_characters(self):
        base = dict(mac="000b82123456", extension="120", display_name="Office",
                    sip_password="secret", server_host="pbx.example")
        for key, value in (("mac", "bad"), ("extension", "12"), ("server_host", "https://pbx"),
                           ("display_name", "Office\n<item name='evil'>1</item>"),
                           ("sip_password", "secret\nP2=bad"), ("server_port", 70000)):
            args = {**base, key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                render_grandstream_xml(**args)


if __name__ == "__main__":
    unittest.main()
