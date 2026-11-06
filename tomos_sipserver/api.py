"""Authenticated FastAPI extension-management API for TomOS SIP Server."""
from __future__ import annotations

import hmac
import os
import re
import secrets
import sqlite3
import subprocess
import tempfile
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from .db import DEFAULT_DB, connect, initialize_database

EXTENSION_PATTERN = r"^[1-9][0-9]{2,5}$"
PASSWORD_PATTERN = r"^[A-Za-z0-9._~-]{24,128}$"
security = HTTPBearer(auto_error=False)


class ExtensionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    extension: str = Field(pattern=EXTENSION_PATTERN)
    display_name: str = Field(min_length=1, max_length=64)
    password: str | None = Field(default=None, pattern=PASSWORD_PATTERN)


class ExtensionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None, min_length=1, max_length=64)
    enabled: bool | None = None


class ExtensionView(BaseModel):
    extension: str
    display_name: str
    enabled: bool
    created_at: str
    updated_at: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _view(row: sqlite3.Row) -> dict:
    return {
        "extension": row["extension"],
        "display_name": row["display_name"],
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _apply_config() -> None:
    result = subprocess.run(
        ["/usr/bin/sudo", "-n", "/usr/local/sbin/tomos-sipserver-apply-config"],
        check=False, capture_output=True, text=True, timeout=45,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Configuration apply failed").strip()
        raise RuntimeError(detail[-1000:])


def create_app(
    database_path: str | Path | None = None,
    api_token: str | None = None,
    apply_callback: Callable[[], None] | None = None,
) -> FastAPI:
    db_path = Path(database_path or os.environ.get("TOMOS_DB_PATH", str(DEFAULT_DB)))
    token = api_token if api_token is not None else os.environ.get("TOMOS_API_TOKEN", "")
    if not token or len(token) < 32:
        raise RuntimeError("TOMOS_API_TOKEN must be configured with at least 32 characters")
    initialize_database(db_path)
    apply = apply_callback or _apply_config
    mutation_lock = threading.RLock()
    app = FastAPI(
        title="TomOS SIP Server API",
        version="0.2.0",
        description="Authenticated LAN administration API. SIP passwords are only returned when created or reset.",
        docs_url="/docs",
        redoc_url=None,
    )

    def require_token(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> None:
        supplied = credentials.credentials if credentials and credentials.scheme.lower() == "bearer" else ""
        if not supplied or not hmac.compare_digest(supplied, token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API token", headers={"WWW-Authenticate": "Bearer"})

    def mutate(operation: Callable[[sqlite3.Connection], object]):
        with mutation_lock:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            fd, snapshot_name = tempfile.mkstemp(prefix="tomos-db-rollback-")
            os.close(fd)
            snapshot = Path(snapshot_name)
            try:
                with connect(db_path) as current, closing(sqlite3.connect(snapshot)) as backup:
                    current.backup(backup)
                    result = operation(current)
                    current.commit()
                try:
                    apply()
                except Exception as error:
                    with closing(sqlite3.connect(snapshot)) as backup, connect(db_path) as current:
                        backup.backup(current)
                    raise HTTPException(status_code=503, detail=f"Asterisk configuration could not be applied; database change rolled back: {error}") from error
                return result
            finally:
                snapshot.unlink(missing_ok=True)

    @app.get("/health", tags=["health"])
    def health() -> dict:
        return {"status": "ok", "service": "tomos-sipserver-api"}

    @app.get("/api/v1/extensions", response_model=list[ExtensionView], dependencies=[Depends(require_token)])
    def list_extensions():
        with connect(db_path) as connection:
            rows = connection.execute("SELECT * FROM extensions ORDER BY extension").fetchall()
        return [_view(row) for row in rows]

    @app.post("/api/v1/extensions", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_token)])
    def create_extension(payload: ExtensionCreate):
        display_name = payload.display_name.strip()
        if not display_name:
            raise HTTPException(status_code=422, detail="display_name cannot be blank")
        password = payload.password or secrets.token_urlsafe(32)
        now = _utc_now()
        def operation(connection):
            try:
                connection.execute(
                    "INSERT INTO extensions(extension, display_name, password, enabled, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
                    (payload.extension, display_name, password, now, now),
                )
            except sqlite3.IntegrityError as error:
                raise HTTPException(status_code=409, detail="Extension already exists") from error
            return {"extension": payload.extension, "display_name": display_name, "enabled": True, "sip_password": password}
        return mutate(operation)

    @app.patch("/api/v1/extensions/{extension}", dependencies=[Depends(require_token)])
    def update_extension(extension: str, payload: ExtensionUpdate):
        if not re.fullmatch(EXTENSION_PATTERN, extension):
            raise HTTPException(status_code=422, detail="Invalid extension number")
        changes = payload.model_dump(exclude_unset=True)
        if not changes or any(value is None for value in changes.values()):
            raise HTTPException(status_code=422, detail="Provide a non-null display_name or enabled value")
        if "display_name" in changes:
            changes["display_name"] = changes["display_name"].strip()
            if not changes["display_name"]:
                raise HTTPException(status_code=422, detail="display_name cannot be blank")
        def operation(connection):
            row = connection.execute("SELECT * FROM extensions WHERE extension = ?", (extension,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Extension not found")
            fields = []
            values = []
            for key, value in changes.items():
                fields.append(f"{key} = ?")
                values.append(int(value) if key == "enabled" else value)
            fields.append("updated_at = ?")
            values.extend([_utc_now(), extension])
            connection.execute(f"UPDATE extensions SET {', '.join(fields)} WHERE extension = ?", values)
            updated = connection.execute("SELECT * FROM extensions WHERE extension = ?", (extension,)).fetchone()
            return _view(updated)
        return mutate(operation)

    @app.delete("/api/v1/extensions/{extension}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_token)])
    def delete_extension(extension: str):
        if not re.fullmatch(EXTENSION_PATTERN, extension):
            raise HTTPException(status_code=422, detail="Invalid extension number")
        def operation(connection):
            cursor = connection.execute("DELETE FROM extensions WHERE extension = ?", (extension,))
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Extension not found")
        mutate(operation)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    def rotate_password(extension: str):
        if not re.fullmatch(EXTENSION_PATTERN, extension):
            raise HTTPException(status_code=422, detail="Invalid extension number")
        password = secrets.token_urlsafe(32)
        def operation(connection):
            cursor = connection.execute("UPDATE extensions SET password = ?, updated_at = ? WHERE extension = ?", (password, _utc_now(), extension))
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Extension not found")
            return {"extension": extension, "sip_password": password}
        return mutate(operation)

    @app.post("/api/v1/extensions/{extension}/password", dependencies=[Depends(require_token)])
    def reset_password(extension: str):
        return rotate_password(extension)

    @app.post("/api/v1/extensions/{extension}/enable", dependencies=[Depends(require_token)])
    def enable_extension(extension: str):
        return update_extension(extension, ExtensionUpdate(enabled=True))

    @app.post("/api/v1/extensions/{extension}/disable", dependencies=[Depends(require_token)])
    def disable_extension(extension: str):
        return update_extension(extension, ExtensionUpdate(enabled=False))

    return app
