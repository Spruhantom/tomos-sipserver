import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tomos_sipserver.auth import hash_password, verify_password
from tomos_sipserver.db import connect, initialize_database
from tomos_sipserver.installer_credentials import update_bootstrap_admin_credentials


class InstallerCredentialUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Path(self.tmp.name) / "extensions.db"
        initialize_database(self.database)
        now = datetime.now(timezone.utc).isoformat()
        with connect(self.database) as connection:
            connection.execute(
                """INSERT INTO admin_users
                   (username, password_hash, role, enabled, created_at, updated_at)
                   VALUES (?, ?, 'admin', 1, ?, ?)""",
                ("admin", hash_password("original-admin-password-123"), now, now),
            )

    def tearDown(self):
        self.tmp.cleanup()

    def read_admin(self):
        with connect(self.database) as connection:
            return connection.execute(
                "SELECT username, password_hash FROM admin_users"
            ).fetchone()

    def test_explicit_upgrade_changes_username_and_password(self):
        update_bootstrap_admin_credentials(
            self.database, "admin", "new-admin", "replacement-admin-password-456"
        )
        row = self.read_admin()
        self.assertEqual(row["username"], "new-admin")
        self.assertTrue(verify_password("replacement-admin-password-456", row["password_hash"]))
        self.assertFalse(verify_password("original-admin-password-123", row["password_hash"]))

    def test_username_only_change_preserves_dashboard_password(self):
        original_hash = self.read_admin()["password_hash"]
        update_bootstrap_admin_credentials(self.database, "admin", "new-admin")
        row = self.read_admin()
        self.assertEqual(row["username"], "new-admin")
        self.assertEqual(row["password_hash"], original_hash)

    def test_missing_bootstrap_account_fails_without_creating_an_account(self):
        with self.assertRaises(LookupError):
            update_bootstrap_admin_credentials(self.database, "missing", "new-admin")
        with connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0], 1)

    def test_duplicate_username_rolls_back(self):
        now = datetime.now(timezone.utc).isoformat()
        with connect(self.database) as connection:
            connection.execute(
                """INSERT INTO admin_users
                   (username, password_hash, role, enabled, created_at, updated_at)
                   VALUES (?, ?, 'operator', 1, ?, ?)""",
                ("taken", hash_password("operator-password-123"), now, now),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            update_bootstrap_admin_credentials(self.database, "admin", "taken")
        self.assertEqual(self.read_admin()["username"], "admin")


if __name__ == "__main__":
    unittest.main()
