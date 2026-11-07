import unittest
import xml.etree.ElementTree as ET

from tomos_sipserver.cisco import normalize_model, render_phone_xml, render_sccp_config


class CiscoRendererTests(unittest.TestCase):
    def test_model_allowlist_and_normalization(self):
        self.assertEqual(normalize_model("Cisco 7960"), "7960")
        self.assertEqual(normalize_model("7970"), "7970")
        with self.assertRaises(ValueError):
            normalize_model("8841")

    def test_sccp_sections_map_device_to_line_without_sip_secret(self):
        config = render_sccp_config(
            mac="00:15:65:aa:bb:cc", model="7940", extension="100",
            display_name="Front Desk", server_host="192.168.1.10",
            sip_password="a" * 32,
        )
        self.assertIn("[SEP001565AABBCC]", config)
        self.assertIn("devicetype = 7940", config)
        self.assertIn("button = line, 100", config)
        self.assertIn("[100]", config)
        self.assertIn("context = tomos-internal", config)
        self.assertNotIn("a" * 32, config)

    def test_phone_xml_contains_call_manager_and_is_well_formed(self):
        xml = render_phone_xml(mac="00:15:65:aa:bb:cc", server_host="192.168.1.10")
        root = ET.fromstring(xml)
        self.assertEqual(root.tag, "device")
        self.assertEqual(root.findtext("./devicePool/callManagerGroup/members/member/callManager/processNodeName"),
                         "192.168.1.10")
        self.assertEqual(root.findtext("./devicePool/callManagerGroup/members/member/callManager/ports/ethernetPhonePort"),
                         "2000")

    def test_display_name_cannot_inject_sccp_sections(self):
        with self.assertRaises(ValueError):
            render_sccp_config(
                mac="00:15:65:aa:bb:cc", model="7960", extension="100",
                display_name="Front Desk\\n[evil]", server_host="192.168.1.10",
            )

    def test_invalid_server_host_and_extension_are_rejected(self):
        with self.assertRaises(ValueError):
            render_phone_xml(mac="00:15:65:aa:bb:cc", server_host="https://pbx.example")
        with self.assertRaises(ValueError):
            render_sccp_config(
                mac="00:15:65:aa:bb:cc", model="7960", extension="100;evil",
                display_name="Front Desk", server_host="192.168.1.10",
            )


if __name__ == "__main__":
    unittest.main()
