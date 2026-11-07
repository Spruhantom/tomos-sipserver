"""Synchronise explicitly changed installer credentials with the bootstrap admin account.

Dashboard passwords are normally managed in the UI. This helper is only used
when an installer upgrade explicitly changes the original bootstrap account.
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .auth import hash_password


USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


def update_bootstrap_admin_credentials(
    database_path: str | Path,
    old_username: str,
    new_username: str,
    new_password: str | None = None,
) -> None:
    if not USERNAME_RE.fullmatch(old_username) or not USERNAME_RE.fullmatch(new_username):
        raise ValueError("Invalid administrator username")
    password_hash = hash_password(new_password) if new_password is not None else None
    connection = sqlite3.connect(str(database_path), timeout=10)
    try:
        row = connection.execute(
            "SELECT id FROM admin_users WHERE username = ? COLLATE NOCASE AND role = 'admin'",
            (old_username,),
        ).fetchone()
        if row is None:
            raise LookupError(
                f"Bootstrap administrator {old_username!r} was not found; manage accounts in the dashboard"
            )
        connection.execute(
            """UPDATE admin_users
               SET username = ?, password_hash = COALESCE(?, password_hash), updated_at = ?
               WHERE id = ?""",
            (new_username, password_hash, datetime.now(timezone.utc).isoformat(), row[0]),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--old-username", required=True)
    parser.add_argument("--new-username", required=True)
    parser.add_argument(
        "--update-password", action="store_true",
        help="Read the replacement password from TOMOS_ADMIN_PASSWORD_UPDATE",
    )
    args = parser.parse_args()
    password = os.environ.get("TOMOS_ADMIN_PASSWORD_UPDATE") if args.update_password else None
    if args.update_password and not password:
        parser.error("TOMOS_ADMIN_PASSWORD_UPDATE must be set when --update-password is used")
    update_bootstrap_admin_credentials(
        args.database, args.old_username, args.new_username, password
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
