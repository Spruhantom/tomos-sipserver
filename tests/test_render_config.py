import importlib.util
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "render-config.py"
SPEC = importlib.util.spec_from_file_location("render_config", MODULE_PATH)
render_config = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(render_config)

class RenderConfigTests(unittest.TestCase):
    def test_rejects_bad_extension(self):
        with self.assertRaises(Exception):
            render_config.parse_extension("10;NoOp():abcdefghijklmnopqrstuvwx")

    def test_rejects_short_secret(self):
        with self.assertRaises(Exception):
            render_config.parse_extension("100:short")

    def test_rejects_duplicate_extensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            import sys
            old = sys.argv
            try:
                sys.argv = ["render-config.py", "--output-dir", tmp,
                            "--extension", "100:" + "a" * 24,
                            "--extension", "100:" + "b" * 24]
                with self.assertRaises(SystemExit):
                    render_config.main()
            finally:
                sys.argv = old

    def test_generates_authenticated_pjsip_and_internal_dialplan(self):
        extensions = [("100", "a" * 24), ("101", "b" * 24)]
        pjsip = render_config.render_pjsip(extensions)
        dialplan = render_config.render_dialplan(extensions)
        self.assertIn("[auth-100]", pjsip)
        self.assertIn("password=" + "a" * 24, pjsip)
        self.assertIn("exten => 100,1", dialplan)
        self.assertIn("Dial(PJSIP/101,30)", dialplan)

    def test_generates_dedicated_dtls_srtp_webrtc_endpoint(self):
        pjsip = render_config.render_pjsip(
            [("100", "a" * 24)], [("100", "b" * 24)]
        )
        self.assertIn("[transport-wss]", pjsip)
        self.assertIn("protocol=wss", pjsip)
        self.assertIn("[webrtc-100]", pjsip)
        self.assertIn("username=100-web", pjsip)
        self.assertIn("identify_by=auth_username", pjsip)
        self.assertIn("media_encryption=dtls", pjsip)
        self.assertIn("dtls_auto_generate_cert=yes", pjsip)
        self.assertIn("ice_support=yes", pjsip)
        self.assertIn("rtcp_mux=yes", pjsip)

    def test_generates_uk_numbering_profile_without_enabling_external_calls(self):
        dialplan = render_config.render_dialplan([("100", "a" * 24)])
        self.assertIn("include => tomos-country-outbound", dialplan)
        self.assertIn("exten => _0[1-9]X.,1", dialplan)
        self.assertIn("Set(TOMOS_E164=+44${EXTEN:1})", dialplan)
        self.assertIn("exten => _00X.,1", dialplan)
        self.assertIn("Set(TOMOS_E164=+${EXTEN:2})", dialplan)
        self.assertIn("exten => 999,1", dialplan)
        self.assertIn("exten => 112,1", dialplan)
        self.assertIn("Outbound calls are deliberately blocked", dialplan)
        self.assertNotIn("Dial(PJSIP/${EXTEN}", dialplan)

    def test_international_manual_profile_does_not_add_uk_rules(self):
        dialplan = render_config.render_dialplan([("100", "a" * 24)], country_code="INTL")
        self.assertNotIn("tomos-country-outbound", dialplan)
        self.assertNotIn("+44${EXTEN:1}", dialplan)

    def test_routes_inbound_calls_to_desk_and_browser_clients(self):
        dialplan = render_config.render_dialplan(
            [("100", "a" * 24)], webrtc_extensions=["100"]
        )
        self.assertIn("Dial(PJSIP/100&PJSIP/webrtc-100,30)", dialplan)

    def test_routes_sccp_extensions_to_chan_sccp(self):
        dialplan = render_config.render_dialplan([("100", "a" * 24)], ["200"])
        self.assertIn("Dial(PJSIP/100,30)", dialplan)
        self.assertIn("Dial(SCCP/200,30)", dialplan)
        self.assertNotIn("Dial(PJSIP/200,30)", dialplan)

    def test_keeps_legacy_dual_protocol_extensions_reachable(self):
        dialplan = render_config.render_dialplan([("100", "a" * 24), ("200", "b" * 24)], ["200"])
        self.assertIn("Dial(PJSIP/100,30)", dialplan)
        self.assertIn("Dial(PJSIP/200&SCCP/200,30)", dialplan)

    def test_writes_config_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            render_config.atomic_write(Path(tmp) / "pjsip.conf",
                                       render_config.render_pjsip([("100", "a" * 24)]))
            self.assertTrue((Path(tmp) / "pjsip.conf").exists())

if __name__ == "__main__":
    unittest.main()
