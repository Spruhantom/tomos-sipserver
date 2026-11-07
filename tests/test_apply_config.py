import os
import sqlite3
import tempfile
import unittest
import xml.etree.ElementTree as ET
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
        self.tftp = root / "tftpboot"
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
            connection.execute(
                "INSERT INTO cisco_devices VALUES (?, ?, ?, ?, ?)",
                ("001565AABBDD", "100", "7970", "192.168.1.10", "2026-01-01T00:00:00Z"),
            )
        (self.config / "pjsip.conf").write_text("old pjsip\n")
        (self.config / "extensions.conf").write_text("old dialplan\n")
        (self.config / "tomos-sccp-devices.conf").write_text("old SCCP\n")
        self.patches = [
            patch.object(apply_config, "DB_PATH", self.db),
            patch.object(apply_config, "CONFIG_DIR", self.config),
            patch.object(apply_config, "SCCP_CONFIG_PATH", self.config / "tomos-sccp-devices.conf"),
            patch.object(apply_config, "TFTP_DIR", self.tftp),
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

    def test_renders_enabled_extensions_and_cisco_sccp_and_tftp_profiles(self):
        apply_config.apply(reload_asterisk=False)
        pjsip = (self.config / "pjsip.conf").read_text()
        dialplan = (self.config / "extensions.conf").read_text()
        sccp = (self.config / "tomos-sccp-devices.conf").read_text()
        xml = (self.tftp / "SEP001565AABBCC.cnf.xml").read_text()
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
        self.assertIn("<processNodeName>192.168.1.10</processNodeName>", xml)
        self.assertNotIn("a" * 32, xml)
        self.assertIsNone(ET.fromstring(xml).find("fullConfig"))
        modern_xml = (self.tftp / "SEP001565AABBDD.cnf.xml").read_text()
        modern_root = ET.fromstring(modern_xml)
        self.assertEqual(modern_root.findtext("deviceProtocol"), "SCCP")
        self.assertEqual(modern_root.findtext("fullConfig"), "true")
        self.assertEqual((self.tftp / "SEP001565AABBCC.cnf.xml").stat().st_mode & 0o777, 0o644)

    def test_removes_stale_managed_tftp_profiles(self):
        self.tftp.mkdir()
        stale = self.tftp / "SEPDEADBEEF0001.cnf.xml"
        stale.write_text("stale")
        apply_config.apply(reload_asterisk=False)
        self.assertFalse(stale.exists())

    def test_failed_reload_restores_all_previous_configuration(self):
        self.tftp.mkdir()
        old_profile = self.tftp / "SEP001565AABBCC.cnf.xml"
        old_profile.write_text("old phone profile")
        with patch.object(apply_config, "cli", side_effect=[RuntimeError("reload failed"), None, None, None]):
            with self.assertRaisesRegex(RuntimeError, "reload failed"):
                apply_config.apply(reload_asterisk=True)
        self.assertEqual((self.config / "pjsip.conf").read_text(), "old pjsip\n")
        self.assertEqual((self.config / "extensions.conf").read_text(), "old dialplan\n")
        self.assertEqual((self.config / "tomos-sccp-devices.conf").read_text(), "old SCCP\n")
        self.assertEqual(old_profile.read_text(), "old phone profile")


if __name__ == "__main__":
    unittest.main()
