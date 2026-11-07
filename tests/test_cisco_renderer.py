import unittest
import xml.etree.ElementTree as ET

from tomos_sipserver.cisco import (
    CHAN_SCCP_COMMIT, model_compatibility, normalize_model,
    render_phone_xml, render_sccp_config,
)


class CiscoRendererTests(unittest.TestCase):
    def test_model_allowlist_and_normalization(self):
        self.assertEqual(normalize_model("Cisco 7960"), "7960")
        self.assertEqual(normalize_model("7970"), "7970")
        with self.assertRaises(ValueError):
            normalize_model("8841")

    def test_model_compatibility_is_explicit_and_does_not_claim_hardware_test(self):
        legacy = model_compatibility("7940")
        self.assertEqual(legacy["profile_family"], "7940_7960")
        self.assertEqual(legacy["template_source_commit"], CHAN_SCCP_COMMIT)
        self.assertEqual(legacy["firmware_validation"], "manual_required")
        self.assertEqual(legacy["hardware_test_status"], "not_tested")
        modern = model_compatibility("7970")
        self.assertEqual(modern["profile_family"], "797x")
        self.assertEqual(modern["compatibility_status"],
                         "driver_and_profile_supported_hardware_unverified")

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

    def test_7940_and_7960_use_shared_legacy_profile_family(self):
        profiles = []
        for model in ("7940", "7960"):
            root = ET.fromstring(render_phone_xml(
                mac="00:15:65:aa:bb:cc", model=model, server_host="192.168.1.10"
            ))
            profiles.append(root)
            self.assertIsNone(root.find("fullConfig"))
            self.assertEqual(root.findtext("./devicePool/dateTimeSetting/timeZone"),
                             "GMT Standard/Daylight Time")
            self.assertEqual(root.findtext("./devicePool/callManagerGroup/members/member/callManager/processNodeName"),
                             "192.168.1.10")
            self.assertEqual(root.findtext("./devicePool/callManagerGroup/members/member/callManager/ports/ethernetPhonePort"),
                             "2000")
            self.assertIsNotNone(root.find("./devicePool/srstInfo/ipAddr3"))
            self.assertIsNotNone(root.find("./networkLocaleInfo"))
        self.assertEqual(profiles[0].findtext("./vendorConfig/videoCapability"),
                         profiles[1].findtext("./vendorConfig/videoCapability"))

    def test_7970_uses_distinct_797x_profile_structure(self):
        root = ET.fromstring(render_phone_xml(
            mac="00:15:65:aa:bb:cc", model="7970", server_host="192.168.1.10"
        ))
        self.assertEqual(root.findtext("fullConfig"), "true")
        self.assertEqual(root.findtext("deviceProtocol"), "SCCP")
        self.assertEqual(root.findtext("ipAddressMode"), "2")
        self.assertEqual(root.findtext("./devicePool/revertPriority"), "1")
        self.assertEqual(root.findtext("./devicePool/connectionMonitorDuration"), "60")
        self.assertIsNotNone(root.find("commonProfile"))
        self.assertIsNotNone(root.find("./vendorConfig/enableLldpSwPort"))
        self.assertEqual(root.findtext("./devicePool/callManagerGroup/members/member/callManager/processNodeName"),
                         "192.168.1.10")
        self.assertIsNone(root.find("./devicePool/srstInfo/ipAddr3"))

    def test_display_name_cannot_inject_sccp_sections(self):
        with self.assertRaises(ValueError):
            render_sccp_config(
                mac="00:15:65:aa:bb:cc", model="7960", extension="100",
                display_name="Front Desk\\n[evil]", server_host="192.168.1.10",
            )

    def test_invalid_server_host_extension_and_sccp_port_are_rejected(self):
        with self.assertRaises(ValueError):
            render_phone_xml(mac="00:15:65:aa:bb:cc", model="7970",
                             server_host="https://pbx.example")
        with self.assertRaises(ValueError):
            render_sccp_config(
                mac="00:15:65:aa:bb:cc", model="7960", extension="100;evil",
                display_name="Front Desk", server_host="192.168.1.10",
            )
        with self.assertRaises(ValueError):
            render_phone_xml(mac="00:15:65:aa:bb:cc", model="7970",
                             server_host="192.168.1.10", sccp_port=0)


if __name__ == "__main__":
    unittest.main()
