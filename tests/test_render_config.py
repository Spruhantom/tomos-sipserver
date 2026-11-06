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
        self.assertIn("Dial(PJSIP/${EXTEN},30)", dialplan)

    def test_writes_config_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            render_config.atomic_write(Path(tmp) / "pjsip.conf",
                                       render_config.render_pjsip([("100", "a" * 24)]))
            self.assertTrue((Path(tmp) / "pjsip.conf").exists())

if __name__ == "__main__":
    unittest.main()
