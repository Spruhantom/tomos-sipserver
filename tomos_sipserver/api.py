"""Authenticated FastAPI extension-management API for TomOS SIP Server."""
from __future__ import annotations

import csv
import hashlib
import io
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

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from .db import DEFAULT_DB, connect, initialize_database

EXTENSION_PATTERN = r"^[1-9][0-9]{2,5}$"
PASSWORD_PATTERN = r"^[A-Za-z0-9._~-]{24,128}$"
SESSION_MAX_AGE = 28800
security = HTTPBearer(auto_error=False)


class DashboardLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=256)


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


def _asterisk_status() -> dict:
    """Read a sanitised Asterisk status snapshot through the fixed root helper."""
    try:
        result = subprocess.run(["/usr/bin/sudo", "-n", "/usr/local/sbin/tomos-sipserver-status"], check=False, capture_output=True, text=True, timeout=12)
        if result.returncode != 0:
            return {"status":"offline","uptime":None,"registered_contacts":[],"registered_count":0}
        import json
        payload = json.loads(result.stdout)
        contacts = payload.get("registered_contacts", [])
        if not isinstance(contacts, list):
            raise ValueError("Invalid contact list")
        safe = []
        for item in contacts:
            if isinstance(item, dict) and re.fullmatch(EXTENSION_PATTERN, str(item.get("extension", ""))):
                safe.append({"extension":str(item["extension"]), "status":str(item.get("status","Unknown"))[:24]})
        return {"status":"online" if payload.get("status")=="online" else "offline", "uptime":str(payload.get("uptime") or "")[:160] or None, "registered_contacts":safe, "registered_count":len(safe)}
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        return {"status":"offline","uptime":None,"registered_contacts":[],"registered_count":0}


def _call_filter_sql(start_date: str | None, end_date: str | None, number: str | None, disposition: str | None):
    clauses = []
    values = []
    if start_date:
        clauses.append("calldate >= ?")
        values.append(start_date)
    if end_date:
        from datetime import date, timedelta
        clauses.append("calldate < ?")
        values.append((date.fromisoformat(end_date) + timedelta(days=1)).isoformat())
    if number:
        escaped = number.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append("(src LIKE ? ESCAPE '\\\\' OR dst LIKE ? ESCAPE '\\\\' OR clid LIKE ? ESCAPE '\\\\')")
        pattern = f"%{escaped}%"
        values.extend([pattern, pattern, pattern])
    if disposition:
        clauses.append("UPPER(disposition) = ?")
        values.append(disposition)
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), values


def _read_call_history(path: Path, limit: int, offset: int, start_date: str | None = None,
                       end_date: str | None = None, number: str | None = None,
                       disposition: str | None = None) -> dict:
    """Read filtered CDR rows from Asterisk's separate SQLite database without write access."""
    if not path.is_file():
        return {"calls": [], "total": 0, "limit": limit, "offset": offset}
    where, values = _call_filter_sql(start_date, end_date, number, disposition)
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            total = connection.execute("SELECT COUNT(*) FROM cdr" + where, values).fetchone()[0]
            rows = connection.execute(
                "SELECT calldate, clid, src, dst, duration, billsec, disposition FROM cdr" +
                where + " ORDER BY calldate DESC LIMIT ? OFFSET ?",
                [*values, limit, offset],
            ).fetchall()
        calls = []
        for row in rows:
            calls.append({
                "started_at": str(row["calldate"] or "")[:40],
                "caller_id": str(row["clid"] or "")[:128],
                "source": str(row["src"] or "")[:64],
                "destination": str(row["dst"] or "")[:64],
                "duration_seconds": max(0, int(float(row["duration"] or 0))),
                "billable_seconds": max(0, int(float(row["billsec"] or 0))),
                "disposition": str(row["disposition"] or "UNKNOWN")[:24].upper(),
            })
        return {"calls": calls, "total": total, "limit": limit, "offset": offset}
    except (sqlite3.Error, ValueError, TypeError) as error:
        raise HTTPException(status_code=503, detail="Call history is temporarily unavailable") from error


def _csv_safe(value: object) -> str:
    text = str(value if value is not None else "")
    return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


def create_app(
    database_path: str | Path | None = None,
    api_token: str | None = None,
    apply_callback: Callable[[], None] | None = None,
    admin_password: str | None = None,
    static_dir: str | Path | None = None,
    status_callback: Callable[[], dict] | None = None,
    cdr_database_path: str | Path | None = None,
) -> FastAPI:
    db_path = Path(database_path or os.environ.get("TOMOS_DB_PATH", str(DEFAULT_DB)))
    token = api_token if api_token is not None else os.environ.get("TOMOS_API_TOKEN", "")
    if not token or len(token) < 32:
        raise RuntimeError("TOMOS_API_TOKEN must be configured with at least 32 characters")
    initialize_database(db_path)
    apply = apply_callback or _apply_config
    get_status = status_callback or _asterisk_status
    cdr_path = Path(cdr_database_path or os.environ.get("TOMOS_CDR_DB_PATH", "/var/lib/tomos-sipserver/cdr/cdr.db"))
    admin_secret = admin_password if admin_password is not None else os.environ.get("TOMOS_ADMIN_PASSWORD", "")
    if len(admin_secret) < 24:
        raise RuntimeError("TOMOS_ADMIN_PASSWORD must be configured with at least 24 characters")
    cookie_secure = os.environ.get("TOMOS_COOKIE_SECURE", "false").lower() in {"1", "true", "yes"}
    dashboard_dir = Path(static_dir or os.environ.get("TOMOS_DASHBOARD_DIR", "/opt/tomos-sipserver/app/dashboard"))
    mutation_lock = threading.RLock()
    app = FastAPI(
        title="TomOS SIP Server API",
        version="0.2.0",
        description="Authenticated LAN administration API. SIP passwords are only returned when created or reset.",
        docs_url="/docs",
        redoc_url=None,
    )

    def valid_session(request: Request, check_csrf: bool = False) -> bool:
        raw = request.cookies.get("tomos_session", "")
        try:
            issued, csrf, signature = raw.split(".", 2)
            signed = f"{issued}.{csrf}"
            expected = hmac.new(token.encode(), signed.encode(), hashlib.sha256).hexdigest()
            age = int(datetime.now(timezone.utc).timestamp()) - int(issued)
            if age < 0 or age > SESSION_MAX_AGE or not hmac.compare_digest(signature, expected):
                return False
            if check_csrf:
                header = request.headers.get("x-tomos-csrf", "")
                cookie = request.cookies.get("tomos_csrf", "")
                if not header or not cookie or not hmac.compare_digest(header, csrf) or not hmac.compare_digest(cookie, csrf):
                    return False
            return True
        except (ValueError, TypeError):
            return False

    def require_token(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> None:
        supplied = credentials.credentials if credentials and credentials.scheme.lower() == "bearer" else ""
        if supplied and hmac.compare_digest(supplied, token):
            return
        if valid_session(request, check_csrf=request.method not in {"GET", "HEAD", "OPTIONS"}):
            return
        raise HTTPException(status_code=401, detail="Login required or session expired", headers={"WWW-Authenticate": "Bearer"})

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

    @app.post("/api/v1/auth/login", tags=["authentication"])
    def dashboard_login(payload: DashboardLogin, response: Response):
        if not hmac.compare_digest(payload.password, admin_secret):
            raise HTTPException(status_code=401, detail="Invalid password")
        issued = str(int(datetime.now(timezone.utc).timestamp()))
        csrf = secrets.token_urlsafe(24)
        signed = f"{issued}.{csrf}"
        signature = hmac.new(token.encode(), signed.encode(), hashlib.sha256).hexdigest()
        response.set_cookie("tomos_session", f"{signed}.{signature}", max_age=SESSION_MAX_AGE, httponly=True, secure=cookie_secure, samesite="strict", path="/")
        response.set_cookie("tomos_csrf", csrf, max_age=SESSION_MAX_AGE, httponly=False, secure=cookie_secure, samesite="strict", path="/")
        return {"authenticated": True, "expires_in": SESSION_MAX_AGE}

    @app.post("/api/v1/auth/logout", tags=["authentication"])
    def dashboard_logout(response: Response):
        response.delete_cookie("tomos_session", path="/", secure=cookie_secure, httponly=True, samesite="strict")
        response.delete_cookie("tomos_csrf", path="/", secure=cookie_secure, samesite="strict")
        return {"authenticated": False}

    @app.get("/api/v1/auth/session", tags=["authentication"])
    def dashboard_session(request: Request):
        return {"authenticated": valid_session(request)}

    @app.get("/api/v1/dashboard/summary", dependencies=[Depends(require_token)], tags=["dashboard"])
    def dashboard_summary():
        with connect(db_path) as connection:
            total = connection.execute("SELECT COUNT(*) FROM extensions").fetchone()[0]
            enabled = connection.execute("SELECT COUNT(*) FROM extensions WHERE enabled = 1").fetchone()[0]
        return {"api": "online", "asterisk": "not_checked", "extensions_total": total, "extensions_enabled": enabled}

    @app.get("/api/v1/dashboard/status", dependencies=[Depends(require_token)], tags=["dashboard"])
    def dashboard_status():
        payload = get_status()
        contacts = payload.get("registered_contacts", []) if isinstance(payload, dict) else []
        safe = []
        if isinstance(contacts, list):
            for item in contacts:
                if isinstance(item, dict) and re.fullmatch(EXTENSION_PATTERN, str(item.get("extension", ""))):
                    safe.append({"extension": str(item["extension"]), "status": str(item.get("status", "Unknown"))[:24]})
        return {"status": "online" if isinstance(payload, dict) and payload.get("status") == "online" else "offline",
                "uptime": str(payload.get("uptime") or "")[:160] or None if isinstance(payload, dict) else None,
                "registered_contacts": safe, "registered_count": len(safe)}

    def call_filters(start_date: str | None, end_date: str | None, number: str | None, disposition: str | None):
        from datetime import date
        for label, value in (("start_date", start_date), ("end_date", end_date)):
            if value:
                try:
                    date.fromisoformat(value)
                except ValueError as error:
                    raise HTTPException(status_code=422, detail=f"{label} must be a real YYYY-MM-DD date") from error
        if start_date and end_date and start_date > end_date:
            raise HTTPException(status_code=422, detail="start_date must not be after end_date")
        if number is not None and not number.strip():
            raise HTTPException(status_code=422, detail="number filter cannot be blank")
        if disposition and disposition not in {"ANSWERED", "NO ANSWER", "BUSY", "FAILED"}:
            raise HTTPException(status_code=422, detail="Unsupported call disposition")
        return number.strip() if number else None

    @app.get("/api/v1/calls", dependencies=[Depends(require_token)], tags=["call history"])
    def call_history(
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=100000),
        start_date: str | None = Query(default=None, pattern=r"^\\d{4}-\\d{2}-\\d{2}$"),
        end_date: str | None = Query(default=None, pattern=r"^\\d{4}-\\d{2}-\\d{2}$"),
        number: str | None = Query(default=None, max_length=64),
        disposition: str | None = Query(default=None, max_length=24),
    ):
        number_filter = call_filters(start_date, end_date, number, disposition.upper() if disposition else None)
        return _read_call_history(cdr_path, limit, offset, start_date, end_date, number_filter,
                                  disposition.upper() if disposition else None)

    @app.get("/api/v1/calls/export.csv", dependencies=[Depends(require_token)], tags=["call history"])
    def export_call_history(
        start_date: str | None = Query(default=None, pattern=r"^\\d{4}-\\d{2}-\\d{2}$"),
        end_date: str | None = Query(default=None, pattern=r"^\\d{4}-\\d{2}-\\d{2}$"),
        number: str | None = Query(default=None, max_length=64),
        disposition: str | None = Query(default=None, max_length=24),
    ):
        number_filter = call_filters(start_date, end_date, number, disposition.upper() if disposition else None)
        result = _read_call_history(cdr_path, 5000, 0, start_date, end_date, number_filter,
                                    disposition.upper() if disposition else None)
        if result["total"] > 5000:
            raise HTTPException(status_code=413, detail="Narrow the filters to export no more than 5,000 call records")
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["Started at", "Caller ID", "Source", "Destination", "Duration seconds", "Billable seconds", "Disposition"])
        for call in result["calls"]:
            writer.writerow([_csv_safe(call[key]) for key in (
                "started_at", "caller_id", "source", "destination", "duration_seconds",
                "billable_seconds", "disposition")])
        return Response(content=output.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="tomos-call-history.csv"'})

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

    if dashboard_dir.is_dir():
        app.mount("/dashboard", StaticFiles(directory=dashboard_dir, html=True), name="dashboard")

    @app.get("/", include_in_schema=False)
    def dashboard_root():
        index = dashboard_dir / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=404, detail="Dashboard assets are not installed")
        return FileResponse(index)

    return app
