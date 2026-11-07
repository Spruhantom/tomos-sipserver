import unittest
import xml.etree.ElementTree as ET

from tomos_sipserver.polycom import normalize_model, render_polycom_cfg


class PolycomRendererTests(unittest.TestCase):
    def test_vvx_model_allowlist(self):
        self.assertEqual(normalize_model("VVX 450"), "VVX450")
        self.assertEqual(normalize_model("vvx-250"), "VVX250")
        with self.assertRaises(ValueError):
            normalize_model("CCX350")

    def test_rendered_cfg_contains_vvx_registration(self):
        cfg = render_polycom_cfg(
            mac="00:04:f2:12:34:56", extension="120", display_name="Front Desk",
            sip_password="secret&safe", server_host="pbx.example",
        )
        root = ET.fromstring(cfg)
        reg = root.find("reg")
        self.assertIsNotNone(reg)
        self.assertEqual(reg.attrib["reg.1.address"], "120")
        self.assertEqual(reg.attrib["reg.1.auth.userId"], "120")
        self.assertEqual(reg.attrib["reg.1.auth.password"], "secret&safe")
        self.assertEqual(reg.attrib["reg.1.server.1.address"], "pbx.example")
        self.assertEqual(reg.attrib["reg.1.server.1.port"], "5060")
        self.assertEqual(reg.attrib["reg.1.server.1.transport"], "UDP")

    def test_transport_and_xml_escaping(self):
        cfg = render_polycom_cfg(
            mac="0004f2123456", extension="120", display_name="Desk & Office",
            sip_password="secret", server_host="pbx.example", transport="TLS",
        )
        reg = ET.fromstring(cfg).find("reg")
        self.assertEqual(reg.attrib["reg.1.label"], "Desk & Office")
        self.assertEqual(reg.attrib["reg.1.server.1.transport"], "TLS")

    def test_invalid_values_are_rejected(self):
        base = dict(mac="0004f2123456", extension="120", display_name="Office",
                    sip_password="secret", server_host="pbx.example")
        for key, value in (("mac", "bad"), ("extension", "12"), ("server_host", "https://pbx"),
                           ("display_name", "Office\nreg.2.address=evil"), ("transport", "SCTP"),
                           ("server_port", 70000)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                render_polycom_cfg(**{**base, key: value})


if __name__ == "__main__":
    unittest.main()
