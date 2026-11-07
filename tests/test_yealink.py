import unittest

from tomos_sipserver.yealink import normalize_mac, render_yealink_cfg, validate_server_host


class YealinkRendererTests(unittest.TestCase):
    def test_mac_normalization_accepts_common_separators(self):
        self.assertEqual(normalize_mac("00:15:65:aa:bb:cc"), "001565AABBCC")
        self.assertEqual(normalize_mac("00-15-65-AA-BB-CC"), "001565AABBCC")

    def test_invalid_mac_is_rejected(self):
        for value in ("001122", "00112233445Z", "00:11:22:33:44:55:66", "00:11:22:33:44"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_mac(value)

    def test_host_rejects_url_port_and_path(self):
        for value in ("https://pbx.example", "pbx.example:5060", "pbx.example/path", ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_server_host(value)

    def test_rendered_cfg_contains_expected_single_account_parameters(self):
        config = render_yealink_cfg(
            extension="120", display_name="Office", sip_password="random-secret",
            server_host="192.168.1.10",
        )
        self.assertIn("account.1.enable = 1", config)
        self.assertIn("account.1.auth_name = 120", config)
        self.assertIn("account.1.user_name = 120", config)
        self.assertIn("account.1.password = random-secret", config)
        self.assertIn("account.1.sip_server.1.address = 192.168.1.10", config)
        self.assertIn("account.1.transport_type = 0", config)
        self.assertNotIn("static.auto_provision.server.url", config)

    def test_renderer_rejects_config_line_injection(self):
        with self.assertRaises(ValueError):
            render_yealink_cfg(
                extension="120", display_name="Office\naccount.2.enable = 1",
                sip_password="secret", server_host="pbx.home",
            )


if __name__ == "__main__":
    unittest.main()
