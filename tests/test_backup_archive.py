import hashlib
import importlib.machinery
import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
import sys
from pathlib import Path

BACKUP_PATH = Path(__file__).resolve().parents[1] / "scripts" / "tomos-sipserver-backup"
LOADER = importlib.machinery.SourceFileLoader("tomos_backup", str(BACKUP_PATH))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
BACKUP = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BACKUP
LOADER.exec_module(BACKUP)
verify_archive = BACKUP.verify_archive


class BackupArchiveTests(unittest.TestCase):
    def _archive(self, path: Path, payload: bytes, digest: str | None = None,
                 extra_member: str | None = None):
        checksum = digest or hashlib.sha256(payload).hexdigest()
        manifest = {"format": 1, "created_at": "test", "files": [
            {"name": "pjsip.conf", "sha256": checksum, "size": len(payload)}
        ]}
        with tarfile.open(path, "w:gz") as archive:
            raw = json.dumps(manifest).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
            info = tarfile.TarInfo("payload/pjsip.conf")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
            if extra_member:
                info = tarfile.TarInfo(extra_member)
                info.size = 1
                archive.addfile(info, io.BytesIO(b"x"))

    def test_valid_archive_verifies(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "backup.tar.gz"
            self._archive(archive, b"[transport-udp]")
            result = verify_archive(archive)
            self.assertEqual(result["files"][0]["name"], "pjsip.conf")

    def test_checksum_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "backup.tar.gz"
            self._archive(archive, b"not the same", "0" * 64)
            with self.assertRaises(ValueError):
                verify_archive(archive)

    def test_path_traversal_member_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "backup.tar.gz"
            self._archive(archive, b"safe", extra_member="payload/../../escape")
            with self.assertRaises(ValueError):
                verify_archive(archive)


if __name__ == "__main__":
    unittest.main()
