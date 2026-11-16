import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tomos_sipserver.trunk_store import (
    configure_trunk, load_trunk, public_status, rotate_secret, save_trunk,
)


class TrunkStoreTests(unittest.TestCase):
    def setUp(self):
        self.root_patch = patch("tomos_sipserver.trunk_store.os.geteuid", return_value=0)
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()

    def test_configure_persists_root_only_disabled_trunk(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "etc" / "trunk.json"
            configure_trunk("sip.example.net", "account", "old-secret", path=path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            stored = load_trunk(path)
            self.assertEqual(stored.password, "old-secret")
            self.assertFalse(stored.enabled)
            self.assertFalse(public_status(path)["enabled"])
            self.assertNotIn("password", public_status(path))

    def test_rotate_secret_preserves_provider_settings_and_stays_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trunk.json"
            configure_trunk("sip.example.net", "account", "old-secret",
                            port=5061, transport="tls", outbound_caller_id="+442071234567",
                            path=path)
            updated = rotate_secret("new-secret", path)
            self.assertEqual(updated.password, "new-secret")
            self.assertEqual(updated.port, 5061)
            self.assertEqual(updated.transport, "tls")
            self.assertFalse(updated.enabled)

    def test_rejects_group_or_world_readable_secret_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trunk.json"
            configure_trunk("sip.example.net", "account", "secret", path=path)
            path.chmod(0o644)
            with self.assertRaises(PermissionError):
                load_trunk(path)

    def test_non_root_cannot_read_or_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trunk.json"
            with patch("tomos_sipserver.trunk_store.os.geteuid", return_value=1000):
                with self.assertRaises(PermissionError):
                    load_trunk(path)
                with self.assertRaises(PermissionError):
                    configure_trunk("sip.example.net", "account", "secret", path=path)


if __name__ == "__main__":
    unittest.main()
