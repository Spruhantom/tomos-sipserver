import importlib.util
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tomos_sipserver import restore


class RestoreTests(unittest.TestCase):
    def _backup(self, directory: Path) -> Path:
        import hashlib, io, json, tarfile
        db = directory / "extensions.db"
        connection = sqlite3.connect(db)
        connection.execute("CREATE TABLE extensions (extension TEXT)")
        connection.execute("INSERT INTO extensions VALUES ('100')")
        connection.commit()
        connection.close()
        payload = db.read_bytes()
        manifest = {"format": 1, "created_at": "test", "files": [
            {"name": "extensions.db", "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)}
        ]}
        archive = directory / "backup.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            raw = json.dumps(manifest).encode()
            info = tarfile.TarInfo("manifest.json"); info.size = len(raw)
            bundle.addfile(info, io.BytesIO(raw))
            info = tarfile.TarInfo("payload"); info.type = tarfile.DIRTYPE
            bundle.addfile(info)
            info = tarfile.TarInfo("payload/extensions.db"); info.size = len(payload)
            bundle.addfile(info, io.BytesIO(payload))
        return archive

    def test_restore_stages_verified_database_and_restarts_only_active_services(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"; root.mkdir()
            archive = self._backup(Path(temp))
            events = []
            def control(action, service):
                events.append((action, service))
                return action == "is-active" and service == "asterisk.service"
            result = restore.restore_archive(archive, root=root, service_control=control,
                                             validator=lambda _root: None)
            self.assertEqual(result["restored"], ["extensions.db"])
            db = sqlite3.connect(root / "var/lib/tomos-sipserver/extensions.db")
            self.assertEqual(db.execute("SELECT extension FROM extensions").fetchone()[0], "100")
            self.assertIn(("stop", "asterisk.service"), events)
            self.assertIn(("start", "asterisk.service"), events)
            self.assertNotIn(("start", "tomos-sipserver-api.service"), events)

    def test_failed_validation_rolls_back_existing_file_and_restarts_service(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"; root.mkdir()
            target = root / "var/lib/tomos-sipserver/extensions.db"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"original bytes")
            archive = self._backup(Path(temp))
            events = []
            def control(action, service):
                events.append((action, service))
                return action == "is-active" and service == "asterisk.service"
            def fail(_root):
                raise RuntimeError("validation failed")
            with self.assertRaisesRegex(RuntimeError, "validation failed"):
                restore.restore_archive(archive, root=root, service_control=control, validator=fail)
            self.assertEqual(target.read_bytes(), b"original bytes")
            self.assertIn(("start", "asterisk.service"), events)

    def test_restore_requires_root(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = self._backup(Path(temp))
            original = os.geteuid
            try:
                os.geteuid = lambda: 1000
                with self.assertRaises(PermissionError):
                    restore.restore_archive(archive)
            finally:
                os.geteuid = original


if __name__ == "__main__":
    unittest.main()
