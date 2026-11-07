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
            connection.execute("CREATE TABLE extensions (extension TEXT, password TEXT, enabled INTEGER, display_name TEXT)")
            connection.executemany(
                "INSERT INTO extensions VALUES (?, ?, ?, ?)",
                [("100", "a" * 32, 1, "Front Desk"), ("101", "b" * 32, 0, "Disabled")],
            )
            connection.execute(
                """CREATE TABLE cisco_devices (
                    mac TEXT, extension TEXT, model TEXT, server_host TEXT, created_at TEXT
                )"""
            )
            connection.execute(
                "INSERT INTO cisco_devices VALUES (?, ?, ?, ?, ?)",
                ("001565AABBCC", "100", "7960", "192.168.1.10", "2026-01-01T00:00:00Z"),
            )
        (self.config / "pjsip.conf").write_text("old pjsip\n")
        (self.config / "extensions.conf").write_text("old dialplan\n")
        (self.config / "tomos-sccp-devices.conf").write_text("old SCCP\n")
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

    def test_renders_enabled_extensions_and_cisco_sccp_sections_without_reloading(self):
        apply_config.apply(reload_asterisk=False)
        pjsip = (self.config / "pjsip.conf").read_text()
        dialplan = (self.config / "extensions.conf").read_text()
        sccp = (self.config / "tomos-sccp-devices.conf").read_text()
        self.assertIn("[100]", pjsip)
        self.assertNotIn("[101]", pjsip)
        self.assertIn("exten => 100,1", dialplan)
        self.assertNotIn("exten => 101,1", dialplan)
        self.assertIn("[SEP001565AABBCC]", sccp)
        self.assertIn("devicetype = 7960", sccp)
        self.assertIn("button = line, 100", sccp)
        self.assertIn("[100]", sccp)
        self.assertNotIn("101", sccp)
        self.assertNotIn("password", sccp.lower())

    def test_failed_reload_restores_all_previous_configuration(self):
        with patch.object(apply_config, "cli", side_effect=[RuntimeError("reload failed"), None, None, None]):
            with self.assertRaisesRegex(RuntimeError, "reload failed"):
                apply_config.apply(reload_asterisk=True)
        self.assertEqual((self.config / "pjsip.conf").read_text(), "old pjsip\n")
        self.assertEqual((self.config / "extensions.conf").read_text(), "old dialplan\n")
        self.assertEqual((self.config / "tomos-sccp-devices.conf").read_text(), "old SCCP\n")


if __name__ == "__main__":
    unittest.main()
