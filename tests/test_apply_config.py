import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tomos_sipserver import apply_config


class ApplyConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "extensions.db"
        self.config = root / "asterisk"
        self.config.mkdir()
        self.renderer = Path(__file__).resolve().parents[1] / "scripts" / "render-config.py"
        with sqlite3.connect(self.db) as connection:
            connection.execute("CREATE TABLE extensions (extension TEXT, password TEXT, enabled INTEGER)")
            connection.executemany(
                "INSERT INTO extensions VALUES (?, ?, ?)",
                [("100", "a" * 32, 1), ("101", "b" * 32, 0)],
            )
        (self.config / "pjsip.conf").write_text("old pjsip\n")
        (self.config / "extensions.conf").write_text("old dialplan\n")
        self.patches = [
            patch.object(apply_config, "DB_PATH", self.db),
            patch.object(apply_config, "CONFIG_DIR", self.config),
            patch.object(apply_config, "RENDERER_PATH", self.renderer),
            patch.object(apply_config.os, "geteuid", return_value=0),
            patch.object(apply_config.grp, "getgrnam", return_value=SimpleNamespace(gr_gid=os.getgid())),
            patch.object(apply_config.os, "chown"),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_renders_only_enabled_extensions_without_reloading(self):
        apply_config.apply(reload_asterisk=False)
        pjsip = (self.config / "pjsip.conf").read_text()
        dialplan = (self.config / "extensions.conf").read_text()
        self.assertIn("[100]", pjsip)
        self.assertNotIn("[101]", pjsip)
        self.assertIn("exten => 100,1", dialplan)
        self.assertNotIn("exten => 101,1", dialplan)

    def test_failed_reload_restores_previous_configuration(self):
        with patch.object(apply_config, "cli", side_effect=[RuntimeError("reload failed"), None, None]):
            with self.assertRaisesRegex(RuntimeError, "reload failed"):
                apply_config.apply(reload_asterisk=True)
        self.assertEqual((self.config / "pjsip.conf").read_text(), "old pjsip\n")
        self.assertEqual((self.config / "extensions.conf").read_text(), "old dialplan\n")


if __name__ == "__main__":
    unittest.main()
