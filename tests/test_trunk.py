import unittest

from tomos_sipserver.trunk import Trunk, render_trunk_pjsip, validate_trunk


class TrunkValidationTests(unittest.TestCase):
    def test_disabled_trunk_renders_no_credentials_or_provider(self):
        trunk = Trunk("sip.example.net", "account", "secret", enabled=False)
        rendered = render_trunk_pjsip(trunk)
        self.assertIn("disabled", rendered)
        self.assertNotIn("secret", rendered)
        self.assertNotIn("[tomos-provider-auth]", rendered)

    def test_enabled_trunk_renders_registration_and_auth(self):
        trunk = Trunk("sip.example.net", "account", "secret", enabled=True)
        rendered = render_trunk_pjsip(trunk)
        self.assertIn("[tomos-provider-registration]", rendered)
        self.assertIn("password=secret", rendered)
        self.assertIn("server_uri=sip:sip.example.net:5060", rendered)

    def test_rejects_config_injection(self):
        with self.assertRaises(ValueError):
            render_trunk_pjsip(Trunk("sip.example.net", "account", "x\nallow=all", enabled=True))

    def test_rejects_bad_host_and_transport(self):
        with self.assertRaises(ValueError):
            validate_trunk(Trunk("bad host", "account", "secret"))
        with self.assertRaises(ValueError):
            validate_trunk(Trunk("sip.example.net", "account", "secret", transport="sctp"))

    def test_caller_id_must_be_e164(self):
        with self.assertRaises(ValueError):
            validate_trunk(Trunk("sip.example.net", "account", "secret", enabled=True,
                                 outbound_caller_id="02071234567"))


if __name__ == "__main__":
    unittest.main()
