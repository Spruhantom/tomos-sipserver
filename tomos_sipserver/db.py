"""SQLite storage for TomOS SIP extensions."""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

EXTENSION_RE = re.compile(r"^[1-9][0-9]{2,5}$")
SECRET_RE = re.compile(r"^[A-Za-z0-9._~-]{24,128}$")
DEFAULT_DB = Path("/var/lib/tomos-sipserver/extensions.db")


@contextmanager
def connect(path: str | Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(str(path), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database(path: str | Path = DEFAULT_DB) -> None:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS extensions (
                extension TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                password TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        connection.execute("CREATE INDEX IF NOT EXISTS extensions_enabled_idx ON extensions(enabled)")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS yealink_devices (
                mac TEXT PRIMARY KEY,
                extension TEXT NOT NULL REFERENCES extensions(extension) ON DELETE CASCADE,
                model TEXT NOT NULL,
                server_host TEXT NOT NULL,
                provisioning_password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        connection.execute("CREATE INDEX IF NOT EXISTS yealink_devices_extension_idx ON yealink_devices(extension)")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS grandstream_devices (
                mac TEXT PRIMARY KEY,
                extension TEXT NOT NULL REFERENCES extensions(extension) ON DELETE CASCADE,
                model TEXT NOT NULL,
                server_host TEXT NOT NULL,
                provisioning_password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        connection.execute("CREATE INDEX IF NOT EXISTS grandstream_devices_extension_idx ON grandstream_devices(extension)")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS polycom_devices (
                mac TEXT PRIMARY KEY,
                extension TEXT NOT NULL REFERENCES extensions(extension) ON DELETE CASCADE,
                model TEXT NOT NULL,
                server_host TEXT NOT NULL,
                server_port INTEGER NOT NULL DEFAULT 5060,
                transport TEXT NOT NULL DEFAULT 'UDP',
                provisioning_password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        connection.execute("CREATE INDEX IF NOT EXISTS polycom_devices_extension_idx ON polycom_devices(extension)")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS cisco_devices (
                mac TEXT PRIMARY KEY,
                extension TEXT NOT NULL REFERENCES extensions(extension) ON DELETE CASCADE,
                model TEXT NOT NULL CHECK (model IN ('7940', '7960', '7970')),
                server_host TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        connection.execute("CREATE INDEX IF NOT EXISTS cisco_devices_extension_idx ON cisco_devices(extension)")
    if db_path.exists():
        os.chmod(db_path, 0o600)


def seed_extensions(path: str | Path, extensions: list[tuple[str, str]]) -> None:
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with connect(path) as connection:
        for number, secret in extensions:
            if not EXTENSION_RE.fullmatch(number) or not SECRET_RE.fullmatch(secret):
                raise ValueError("Invalid extension number or SIP password")
            connection.execute(
                "INSERT OR IGNORE INTO extensions(extension, display_name, password, enabled, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
                (number, f"Extension {number}", secret, now, now),
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--seed-extension", action="append", default=[], metavar="NUMBER:SECRET")
    args = parser.parse_args()
    initialize_database(args.database)
    seeds: list[tuple[str, str]] = []
    for value in args.seed_extension:
        if ":" not in value:
            parser.error("--seed-extension must be NUMBER:SECRET")
        seeds.append(tuple(value.split(":", 1)))
    seed_extensions(args.database, seeds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
