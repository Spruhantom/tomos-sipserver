import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts_backup_import_placeholder import verify_archive


class BackupArchiveTests(unittest.TestCase):
    def _archive(self, path: Path, payload: bytes, digest: str | None = None):
        import hashlib
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
            self._archive(archive, b"safe")
            with tarfile.open(archive, "a:gz") as bundle:
                info = tarfile.TarInfo("../escape")
                info.size = 1
                bundle.addfile(info, io.BytesIO(b"x"))
            with self.assertRaises((ValueError, tarfile.TarError)):
                verify_archive(archive)


if __name__ == "__main__":
    unittest.main()
