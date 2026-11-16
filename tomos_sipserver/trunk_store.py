"""Root-only, atomic storage for SIP trunk credentials and settings.

This module deliberately does not activate a trunk. A separate, reviewed
configuration-apply path must validate provider transports and dialplan policy.
"""
from __future__ import annotations

import json
import os
import secrets
import tempfile
from pathlib import Path

from .trunk import Trunk, validate_trunk

DEFAULT_PATH = Path("/etc/tomos-sipserver/trunk.json")


def _require_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError("Trunk credentials may only be managed by root")


def load_trunk(path: Path = DEFAULT_PATH) -> Trunk | None:
    _require_root()
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError("Trunk configuration must be a regular file")
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise PermissionError("Trunk configuration permissions must be 0600")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {
        "host", "username", "password", "port", "transport", "enabled", "outbound_caller_id"
    }:
        raise ValueError("Trunk configuration has an unexpected schema")
    trunk = Trunk(**data)
    validate_trunk(trunk)
    return trunk


def save_trunk(trunk: Trunk, path: Path = DEFAULT_PATH) -> None:
    _require_root()
    validate_trunk(trunk)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    if path.parent.is_symlink():
        raise ValueError("Trunk configuration directory cannot be a symlink")
    fd, temporary = tempfile.mkstemp(prefix=".trunk.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({
                "host": trunk.host,
                "username": trunk.username,
                "password": trunk.password,
                "port": trunk.port,
                "transport": trunk.transport,
                "enabled": False,
                "outbound_caller_id": trunk.outbound_caller_id,
            }, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        Path(temporary).unlink(missing_ok=True)
        raise


def configure_trunk(host: str, username: str, password: str, port: int = 5060,
                    transport: str = "udp", outbound_caller_id: str = "",
                    path: Path = DEFAULT_PATH) -> Trunk:
    trunk = Trunk(host, username, password, port, transport, False, outbound_caller_id)
    save_trunk(trunk, path)
    return trunk


def rotate_secret(password: str, path: Path = DEFAULT_PATH) -> Trunk:
    current = load_trunk(path)
    if current is None:
        raise FileNotFoundError("No SIP trunk has been configured")
    updated = Trunk(current.host, current.username, password, current.port,
                    current.transport, False, current.outbound_caller_id)
    save_trunk(updated, path)
    return updated


def disable_trunk(path: Path = DEFAULT_PATH) -> None:
    current = load_trunk(path)
    if current is None:
        return
    save_trunk(Trunk(current.host, current.username, current.password,
                     current.port, current.transport, False,
                     current.outbound_caller_id), path)


def public_status(path: Path = DEFAULT_PATH) -> dict:
    current = load_trunk(path)
    if current is None:
        return {"configured": False, "enabled": False}
    return {
        "configured": True,
        "enabled": False,
        "host": current.host,
        "username": current.username,
        "port": current.port,
        "transport": current.transport,
        "outbound_caller_id": current.outbound_caller_id or None,
        "secret_configured": bool(current.password),
    }
