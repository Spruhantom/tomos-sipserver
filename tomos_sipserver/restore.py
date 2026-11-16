"""Verified, staged and rollback-capable TomOS SIP Server restore engine."""
from __future__ import annotations
import hashlib
import importlib.machinery
import importlib.util
import os
import pwd
import grp
import sqlite3
import subprocess
import tarfile
import tempfile
from pathlib import Path

TARGETS = {
    "extensions.db": ("/var/lib/tomos-sipserver/extensions.db", "tomos", "tomos", 0o600, "sqlite"),
    "api.env": ("/etc/tomos-sipserver/api.env", "root", "tomos", 0o640, "file"),
    "trunk.json": ("/etc/tomos-sipserver/trunk.json", "root", "root", 0o600, "json"),
    "pjsip.conf": ("/etc/asterisk/pjsip.conf", "root", "asterisk", 0o640, "file"),
    "extensions.conf": ("/etc/asterisk/extensions.conf", "root", "asterisk", 0o640, "file"),
    "http.conf": ("/etc/asterisk/http.conf", "root", "asterisk", 0o640, "file"),
    "rtp.conf": ("/etc/asterisk/rtp.conf", "root", "asterisk", 0o640, "file"),
    "sccp.conf": ("/etc/asterisk/sccp.conf", "root", "asterisk", 0o640, "file"),
    "tomos-sccp-devices.conf": ("/etc/asterisk/tomos-sccp-devices.conf", "root", "asterisk", 0o640, "file"),
    "cdr.db": ("/var/lib/tomos-sipserver/cdr/cdr.db", "root", "tomos-cdr", 0o660, "sqlite"),
}
SERVICES = ("tomos-sipserver-api.service", "asterisk.service", "tomos-sipserver.service")


def _verify_archive(archive: Path) -> dict:
    script = Path(__file__).resolve().parents[1] / "scripts" / "tomos-sipserver-backup"
    loader = importlib.machinery.SourceFileLoader("tomos_backup_verify", str(script))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = module
    loader.exec_module(module)
    return module.verify_archive(archive)


def _sqlite_ok(data: bytes) -> None:
    with tempfile.NamedTemporaryFile() as handle:
        handle.write(data)
        handle.flush()
        db = sqlite3.connect(f"file:{handle.name}?mode=ro", uri=True)
        try:
            result = db.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise ValueError("SQLite integrity check failed")
        finally:
            db.close()


def _atomic_write(path: Path, data: bytes, owner: str, group: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".tomos-restore-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temp_name, pwd.getpwnam(owner).pw_uid, grp.getgrnam(group).gr_gid)
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise


def _systemctl(action: str, service: str) -> bool:
    if action == "is-active":
        result = subprocess.run(["/usr/bin/systemctl", "is-active", "--quiet", service], check=False)
        return result.returncode == 0
    subprocess.run(["/usr/bin/systemctl", action, service], check=True, timeout=60)
    return True


def _validate_live(root: Path) -> None:
    if root != Path("/"):
        return
    for command in ("core show version", "pjsip reload", "dialplan reload"):
        result = subprocess.run(["/usr/sbin/asterisk", "-rx", command],
                                check=False, capture_output=True, text=True, timeout=20)
        output = (result.stdout + result.stderr).strip()
        if result.returncode or "Unable to connect" in output or "No such command" in output:
            raise RuntimeError("Asterisk restore validation failed")


def restore_archive(archive: Path, root: Path = Path("/"), service_control=None,
                    validator=None) -> dict:
    if os.geteuid() != 0:
        raise PermissionError("Restore must run as root")
    manifest = _verify_archive(archive)
    staged = {}
    with tarfile.open(archive, "r:gz") as bundle:
        for entry in manifest["files"]:
            name = entry["name"]
            meta = TARGETS.get(name)
            if meta is None:
                raise ValueError("Backup contains an unsupported restore target")
            member = bundle.getmember("payload/" + name)
            stream = bundle.extractfile(member)
            if stream is None:
                raise ValueError("Backup payload could not be read")
            data = stream.read(1024 * 1024 * 1024 + 1)
            if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise ValueError("Backup changed during restore")
            if meta[4] == "sqlite":
                _sqlite_ok(data)
            elif meta[4] == "json":
                import json
                parsed = json.loads(data)
                if not isinstance(parsed, dict) or set(parsed) != {
                    "host", "username", "password", "port", "transport", "enabled", "outbound_caller_id"
                }:
                    raise ValueError("Trunk configuration is invalid")
                if parsed.get("enabled") is not False:
                    raise ValueError("Restored trunk must remain disabled")
            staged[name] = data
    if "extensions.db" not in staged:
        raise ValueError("Backup does not contain the TomOS extension database")
    paths = {name: root / TARGETS[name][0].lstrip("/") for name in staged}
    previous = {name: (path.read_bytes() if path.is_file() else None,
                       path.stat().st_mode & 0o777 if path.is_file() else None)
                for name, path in paths.items()}
    control = service_control or _systemctl
    validate = validator or _validate_live
    pre_restore_backup = None
    if root == Path("/"):
        script = Path(__file__).resolve().parents[1] / "scripts" / "tomos-sipserver-backup"
        loader = importlib.machinery.SourceFileLoader("tomos_backup_snapshot", str(script))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        import sys
        sys.modules[spec.name] = module
        loader.exec_module(module)
        pre_restore_backup = str(module.build_archive(Path("/var/backups/tomos-sipserver/pre-restore")))
    was_active = {}
    stopped = []
    started = []
    try:
        for service in SERVICES:
            was_active[service] = bool(control("is-active", service))
        for service in SERVICES:
            if was_active[service]:
                control("stop", service)
                stopped.append(service)
        for name, data in staged.items():
            path = paths[name]
            owner, group, mode = TARGETS[name][1:4]
            if root == Path("/"):
                _atomic_write(path, data, owner, group, mode)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                os.chmod(path, mode)
        validate(root)
        if "asterisk.service" in stopped:
            control("start", "asterisk.service")
            started.append("asterisk.service")
            validate(root)
        for service in stopped:
            if service != "asterisk.service":
                control("start", service)
                started.append(service)
        return {"restored": sorted(staged), "services_restarted": stopped,
                "pre_restore_backup": pre_restore_backup}
    except Exception:
        rollback_errors = []
        # A service may already be running against the restored files when a
        # later validation/start step fails. Stop those services before
        # reverting files, otherwise Asterisk can keep the failed config live.
        for service in reversed(started):
            try:
                control("stop", service)
            except Exception as error:
                rollback_errors.append(type(error).__name__)
        for name, (data, mode) in previous.items():
            path = paths[name]
            try:
                if data is None:
                    path.unlink(missing_ok=True)
                elif root == Path("/"):
                    owner, group, target_mode = TARGETS[name][1:4]
                    _atomic_write(path, data, owner, group, mode if mode is not None else target_mode)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                    os.chmod(path, mode if mode is not None else 0o600)
            except Exception as error:
                rollback_errors.append(type(error).__name__)
        for service in stopped:
            try:
                control("start", service)
            except Exception as error:
                rollback_errors.append(type(error).__name__)
        if rollback_errors:
            raise RuntimeError("Restore failed and one or more rollback actions also failed") from None
        raise


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--yes", action="store_true", help="confirm destructive restore")
    args = parser.parse_args()
    if not args.yes:
        parser.error("restore requires explicit --yes confirmation")
    try:
        result = restore_archive(args.archive)
        print("Restore completed and verified: " + ", ".join(result["restored"]))
    except Exception as error:
        print("TomOS restore failed; rollback attempted: " + str(error), file=__import__("sys").stderr)
        raise SystemExit(1)
