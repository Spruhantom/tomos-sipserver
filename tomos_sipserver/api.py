"""Authenticated FastAPI extension-management API for TomOS SIP Server."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import hmac
import os
import re
import secrets
import sqlite3
import subprocess
import tempfile
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, ConfigDict, Field

from .db import DEFAULT_DB, connect, initialize_database
from .auth import hash_password, verify_password
from .yealink import normalize_mac, normalize_model, render_yealink_cfg, validate_server_host
from .grandstream import normalize_model as normalize_grandstream_model, render_grandstream_xml
from .polycom import normalize_model as normalize_polycom_model, render_polycom_cfg
from .cisco import normalize_model as normalize_cisco_model

EXTENSION_PATTERN = r"^[1-9][0-9]{2,5}$"
PASSWORD_PATTERN = r"^[A-Za-z0-9._~-]{24,128}$"
SESSION_MAX_AGE = 28800
security = HTTPBearer(auto_error=False)
provisioning_security = HTTPBasic(auto_error=False)


class DashboardLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=1, max_length=256)



class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=128)
    role: str = Field(default="operator", pattern=r"^(admin|operator|viewer)$")

class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str | None = Field(default=None, pattern=r"^(admin|operator|viewer)$")
    enabled: bool | None = None

class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class UserPasswordUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=12, max_length=128)

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

class YealinkDeviceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    extension: str = Field(pattern=EXTENSION_PATTERN)
    mac: str = Field(min_length=12, max_length=17)
    model: str = Field(default="SIP-T46U", min_length=2, max_length=32, pattern=r"^[A-Za-z0-9-]+$")
    server_host: str = Field(min_length=1, max_length=253)


class GrandstreamDeviceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    extension: str = Field(pattern=EXTENSION_PATTERN)
    mac: str = Field(min_length=12, max_length=17)
    model: str = Field(default="GRP2601", min_length=2, max_length=32, pattern=r"^[A-Za-z0-9-]+$")
    server_host: str = Field(min_length=1, max_length=253)


class CiscoDeviceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    extension: str = Field(pattern=EXTENSION_PATTERN)
    mac: str = Field(min_length=12, max_length=17)
    model: str = Field(min_length=4, max_length=16, pattern=r"^[A-Za-z0-9 -]+$")
    server_host: str = Field(min_length=1, max_length=253)


class PolycomDeviceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    extension: str = Field(pattern=EXTENSION_PATTERN)
    mac: str = Field(min_length=12, max_length=17)
    model: str = Field(default="VVX450", min_length=2, max_length=32, pattern=r"^[A-Za-z0-9 -]+$")
    server_host: str = Field(min_length=1, max_length=253)
    server_port: int = Field(default=5060, ge=1, le=65535)
    transport: str = Field(default="UDP", pattern=r"^(?i:UDP|TCP|TLS)$")




def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_audit(connection: sqlite3.Connection, actor: dict, action: str,
                  target_user_id: int | None = None, target_username: str | None = None,
                  details: dict | None = None) -> None:
    """Append a password-free account audit event in the caller's transaction."""
    connection.execute(
        """INSERT INTO audit_events
           (occurred_at, actor_user_id, actor_username, action, target_user_id,
            target_username, details_json)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (_utc_now(), actor.get("id"), actor.get("username", "unknown"), action,
         target_user_id, target_username, json.dumps(details or {}, sort_keys=True)),
    )


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
        clauses.append("(src LIKE ? ESCAPE '\\' OR dst LIKE ? ESCAPE '\\' OR clid LIKE ? ESCAPE '\\')")
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
    admin_username: str | None = None,
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
    if not re.fullmatch(r"[A-Za-z0-9._~-]{24,128}", admin_secret):
        raise RuntimeError("TOMOS_ADMIN_PASSWORD must be 24-128 safe characters (letters, numbers, dot, underscore, tilde or hyphen)")
    admin_user = admin_username if admin_username is not None else os.environ.get("TOMOS_ADMIN_USERNAME", "admin")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", admin_user):
        raise RuntimeError("TOMOS_ADMIN_USERNAME must be 3-32 letters, numbers, dots, underscores or hyphens")
    with connect(db_path) as connection:
        user_count = connection.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0]
        if user_count == 0:
            now = _utc_now()
            connection.execute("INSERT INTO admin_users(username, password_hash, role, enabled, created_at, updated_at) VALUES (?, ?, 'admin', 1, ?, ?)", (admin_user, hash_password(admin_secret), now, now))
    cookie_secure = os.environ.get("TOMOS_COOKIE_SECURE", "false").lower() in {"1", "true", "yes"}
    dashboard_dir = Path(static_dir or os.environ.get("TOMOS_DASHBOARD_DIR", "/opt/tomos-sipserver/app/dashboard"))
    mutation_lock = threading.RLock()
    login_attempts: dict[str, list[float]] = {}
    login_lock = threading.Lock()
    login_window_seconds = 300
    login_max_attempts = 5
    app = FastAPI(
        title="TomOS SIP Server API",
        version="0.2.0",
        description="Authenticated LAN administration API. SIP passwords are only returned when created or reset.",
        docs_url="/docs",
        redoc_url=None,
    )

    def valid_session(request: Request, check_csrf: bool = False) -> dict | None:
        raw = request.cookies.get("tomos_session", "")
        try:
            issued, user_id_text, csrf, signature = raw.split(".", 3)
            signed = f"{issued}.{user_id_text}.{csrf}"
            expected = hmac.new(token.encode(), signed.encode(), hashlib.sha256).hexdigest()
            age = int(datetime.now(timezone.utc).timestamp()) - int(issued)
            if age < 0 or age > SESSION_MAX_AGE or not hmac.compare_digest(signature, expected):
                return None
            if check_csrf:
                header = request.headers.get("x-tomos-csrf", "")
                cookie = request.cookies.get("tomos_csrf", "")
                if not header or not cookie or not hmac.compare_digest(header, csrf) or not hmac.compare_digest(cookie, csrf):
                    return None
            with connect(db_path) as connection:
                row = connection.execute("SELECT id, username, role, enabled FROM admin_users WHERE id = ?", (int(user_id_text),)).fetchone()
            if row is None or not row["enabled"]:
                return None
            return {"id": row["id"], "username": row["username"], "role": row["role"]}
        except (ValueError, TypeError):
            return None

    def require_token(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> dict:
        supplied = credentials.credentials if credentials and credentials.scheme.lower() == "bearer" else ""
        if supplied and hmac.compare_digest(supplied, token):
            return {"id": None, "username": "api-token", "role": "admin"}
        principal = valid_session(request, check_csrf=request.method not in {"GET", "HEAD", "OPTIONS"})
        if principal is None:
            raise HTTPException(status_code=401, detail="Login required or session expired", headers={"WWW-Authenticate": "Bearer"})
        if principal["role"] == "viewer" and request.method not in {"GET", "HEAD", "OPTIONS"}:
            raise HTTPException(status_code=403, detail="Read-only account")
        return principal

    def require_admin(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> dict:
        principal = require_token(request, credentials)
        if principal["role"] != "admin":
            raise HTTPException(status_code=403, detail="Administrator role required")
        return principal

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
    def dashboard_login(payload: DashboardLogin, request: Request, response: Response):
        client_ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with login_lock:
            attempts = [stamp for stamp in login_attempts.get(client_ip, []) if now - stamp < login_window_seconds]
            if len(attempts) >= login_max_attempts:
                retry_after = max(1, int(login_window_seconds - (now - attempts[0])))
                login_attempts[client_ip] = attempts
                raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.", headers={"Retry-After": str(retry_after)})
            login_attempts[client_ip] = attempts
            with connect(db_path) as connection:
                row = connection.execute("SELECT id, username, password_hash, role, enabled FROM admin_users WHERE username = ? COLLATE NOCASE", (payload.username,)).fetchone()
            if row is None or not row["enabled"] or not verify_password(payload.password, row["password_hash"]):
                login_attempts[client_ip].append(now)
                raise HTTPException(status_code=401, detail="Invalid username or password")
            login_attempts.pop(client_ip, None)
            if len(login_attempts) > 1024:
                expired = [address for address, stamps in login_attempts.items() if not stamps or now - stamps[-1] >= login_window_seconds]
                for address in expired:
                    login_attempts.pop(address, None)
        issued = str(int(datetime.now(timezone.utc).timestamp()))
        csrf = secrets.token_urlsafe(24)
        signed = f"{issued}.{row[0]}.{csrf}"
        signature = hmac.new(token.encode(), signed.encode(), hashlib.sha256).hexdigest()
        response.set_cookie("tomos_session", f"{signed}.{signature}", max_age=SESSION_MAX_AGE, httponly=True, secure=cookie_secure, samesite="strict", path="/")
        response.set_cookie("tomos_csrf", csrf, max_age=SESSION_MAX_AGE, httponly=False, secure=cookie_secure, samesite="strict", path="/")
        return {"authenticated": True, "expires_in": SESSION_MAX_AGE, "user": {"id": row["id"], "username": row["username"], "role": row["role"]}}

    @app.post("/api/v1/auth/logout", tags=["authentication"])
    def dashboard_logout(response: Response):
        response.delete_cookie("tomos_session", path="/", secure=cookie_secure, httponly=True, samesite="strict")
        response.delete_cookie("tomos_csrf", path="/", secure=cookie_secure, samesite="strict")
        return {"authenticated": False}

    @app.get("/api/v1/auth/session", tags=["authentication"])
    def dashboard_session(request: Request):
        principal = valid_session(request)
        return {"authenticated": principal is not None, "user": principal}

    @app.get("/api/v1/users", tags=["user management"])
    def list_users(principal: dict = Depends(require_admin)):
        with connect(db_path) as connection:
            rows = connection.execute("SELECT id, username, role, enabled, created_at, updated_at FROM admin_users ORDER BY username COLLATE NOCASE").fetchall()
        return [{"id": row["id"], "username": row["username"], "role": row["role"], "enabled": bool(row["enabled"]), "created_at": row["created_at"], "updated_at": row["updated_at"]} for row in rows]

    @app.post("/api/v1/users", status_code=status.HTTP_201_CREATED, tags=["user management"])
    def create_user(payload: UserCreate, principal: dict = Depends(require_admin)):
        now = _utc_now()
        try:
            with connect(db_path) as connection:
                cursor = connection.execute("INSERT INTO admin_users(username, password_hash, role, enabled, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)", (payload.username, hash_password(payload.password), payload.role, now, now))
                user_id = cursor.lastrowid
                _record_audit(connection, principal, "user.created", user_id, payload.username,
                              {"role": payload.role, "enabled": True})
        except sqlite3.IntegrityError as error:
            raise HTTPException(status_code=409, detail="Username already exists") from error
        return {"id": user_id, "username": payload.username, "role": payload.role, "enabled": True}

    @app.patch("/api/v1/users/{user_id}", tags=["user management"])
    def update_user(user_id: int, payload: UserUpdate, principal: dict = Depends(require_admin)):
        changes = payload.model_dump(exclude_unset=True)
        if not changes or any(value is None for value in changes.values()):
            raise HTTPException(status_code=422, detail="Provide a non-null role or enabled value")
        with connect(db_path) as connection:
            target = connection.execute("SELECT id, username, role, enabled FROM admin_users WHERE id = ?", (user_id,)).fetchone()
            if target is None:
                raise HTTPException(status_code=404, detail="User not found")
            next_role = changes.get("role", target["role"])
            next_enabled = changes.get("enabled", bool(target["enabled"]))
            if target["role"] == "admin" and target["enabled"] and (next_role != "admin" or not next_enabled):
                remaining = connection.execute("SELECT COUNT(*) FROM admin_users WHERE role = ? AND enabled = 1 AND id != ?", ("admin", user_id)).fetchone()[0]
                if remaining == 0:
                    raise HTTPException(status_code=409, detail="Cannot disable or demote the last active administrator")
            connection.execute("UPDATE admin_users SET role = ?, enabled = ?, updated_at = ? WHERE id = ?", (next_role, int(next_enabled), _utc_now(), user_id))
            if next_role != target["role"]:
                _record_audit(connection, principal, "user.role_changed", user_id, target["username"],
                              {"from": target["role"], "to": next_role})
            if bool(next_enabled) != bool(target["enabled"]):
                _record_audit(connection, principal, "user.enabled" if next_enabled else "user.disabled",
                              user_id, target["username"],
                              {"from": bool(target["enabled"]), "to": bool(next_enabled)})
            row = connection.execute("SELECT id, username, role, enabled FROM admin_users WHERE id = ?", (user_id,)).fetchone()
        return {"id": row["id"], "username": row["username"], "role": row["role"], "enabled": bool(row["enabled"])}

    @app.delete("/api/v1/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["user management"])
    def delete_user(user_id: int, principal: dict = Depends(require_admin)):
        if principal["id"] == user_id:
            raise HTTPException(status_code=409, detail="You cannot delete your own account")
        with connect(db_path) as connection:
            target = connection.execute("SELECT id, role, enabled FROM admin_users WHERE id = ?", (user_id,)).fetchone()
            if target is None:
                raise HTTPException(status_code=404, detail="User not found")
            if target["role"] == "admin" and target["enabled"]:
                remaining = connection.execute("SELECT COUNT(*) FROM admin_users WHERE role = ? AND enabled = 1 AND id != ?", ("admin", user_id)).fetchone()[0]
                if remaining == 0:
                    raise HTTPException(status_code=409, detail="Cannot delete the last active administrator")
            _record_audit(connection, principal, "user.deleted", user_id, target["username"],
                          {"role": target["role"], "enabled": bool(target["enabled"])})
            connection.execute("DELETE FROM admin_users WHERE id = ?", (user_id,))
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/api/v1/users/{user_id}/password", tags=["user management"])
    def reset_user_password(user_id: int, payload: UserPasswordUpdate, principal: dict = Depends(require_admin)):
        with connect(db_path) as connection:
            target = connection.execute("SELECT username FROM admin_users WHERE id = ?", (user_id,)).fetchone()
            if target is None:
                raise HTTPException(status_code=404, detail="User not found")
            connection.execute("UPDATE admin_users SET password_hash = ?, updated_at = ? WHERE id = ?", (hash_password(payload.password), _utc_now(), user_id))
            _record_audit(connection, principal, "user.password_reset", user_id, target["username"])
        return {"id": user_id, "password_updated": True}

    @app.post("/api/v1/auth/password", tags=["authentication"])
    def change_own_password(payload: PasswordChange, principal: dict = Depends(require_token)):
        if principal["id"] is None:
            raise HTTPException(status_code=403, detail="API-token authentication cannot change a dashboard password")
        with connect(db_path) as connection:
            row = connection.execute("SELECT password_hash FROM admin_users WHERE id = ?", (principal["id"],)).fetchone()
            if row is None or not verify_password(payload.old_password, row["password_hash"]):
                raise HTTPException(status_code=400, detail="Current password is incorrect")
            connection.execute("UPDATE admin_users SET password_hash = ?, updated_at = ? WHERE id = ?", (hash_password(payload.new_password), _utc_now(), principal["id"]))
            _record_audit(connection, principal, "user.password_changed", principal["id"], principal["username"])
        return {"password_updated": True}

    @app.get("/api/v1/audit", tags=["audit log"])
    def list_audit_events(
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=1000000),
        action: str | None = Query(default=None, min_length=3, max_length=64, pattern=r"^[a-z_.]+$"),
        principal: dict = Depends(require_admin),
    ):
        """Return newest account audit events; restricted to administrators."""
        where = " WHERE action = ?" if action else ""
        values = [action] if action else []
        with connect(db_path) as connection:
            total = connection.execute("SELECT COUNT(*) FROM audit_events" + where, values).fetchone()[0]
            rows = connection.execute(
                "SELECT id, occurred_at, actor_user_id, actor_username, action, target_user_id, target_username, details_json FROM audit_events" +
                where + " ORDER BY id DESC LIMIT ? OFFSET ?", [*values, limit, offset]
            ).fetchall()
        return {"events": [{
            "id": row["id"], "occurred_at": row["occurred_at"],
            "actor_user_id": row["actor_user_id"], "actor_username": row["actor_username"],
            "action": row["action"], "target_user_id": row["target_user_id"],
            "target_username": row["target_username"], "details": json.loads(row["details_json"]),
        } for row in rows], "total": total, "limit": limit, "offset": offset}

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
        start_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        end_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        number: str | None = Query(default=None, max_length=64),
        disposition: str | None = Query(default=None, max_length=24),
    ):
        number_filter = call_filters(start_date, end_date, number, disposition.upper() if disposition else None)
        return _read_call_history(cdr_path, limit, offset, start_date, end_date, number_filter,
                                  disposition.upper() if disposition else None)

    @app.get("/api/v1/calls/export.csv", dependencies=[Depends(require_token)], tags=["call history"])
    def export_call_history(
        start_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        end_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
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

    @app.post("/api/v1/devices/polycom", status_code=status.HTTP_201_CREATED,
              dependencies=[Depends(require_token)], tags=["Polycom provisioning"])
    def register_polycom_device(payload: PolycomDeviceCreate):
        """Register a Polycom VVX phone and issue one-time provisioning credentials."""
        try:
            mac = normalize_mac(payload.mac)
            host = validate_server_host(payload.server_host)
            model = normalize_polycom_model(payload.model)
            transport = payload.transport.upper()
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        username = "tomos-" + mac.lower()
        password = secrets.token_urlsafe(32)
        now = _utc_now()
        with connect(db_path) as connection:
            extension_row = connection.execute(
                "SELECT enabled FROM extensions WHERE extension = ?", (payload.extension,)
            ).fetchone()
            if extension_row is None:
                raise HTTPException(status_code=404, detail="Extension not found")
            if not extension_row["enabled"]:
                raise HTTPException(status_code=409, detail="Enable the SIP extension before provisioning a phone")
            try:
                connection.execute(
                    """INSERT INTO polycom_devices
                       (mac, extension, model, server_host, server_port, transport,
                        provisioning_password_hash, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (mac, payload.extension, model, host, payload.server_port, transport,
                     hashlib.sha256(password.encode()).hexdigest(), now),
                )
            except sqlite3.IntegrityError as error:
                raise HTTPException(status_code=409, detail="A Polycom device with this MAC address is already registered") from error
        return {
            "mac": mac, "extension": payload.extension, "model": model,
            "provisioning_path": f"/provisioning/polycom/{mac.lower()}.cfg",
            "provisioning_username": username,
            "provisioning_password": password,
            "authentication": "HTTP Basic (use HTTPS)",
            "password_displayed_once": True,
        }

    @app.get("/api/v1/devices/polycom", dependencies=[Depends(require_token)],
             tags=["Polycom provisioning"])
    def list_polycom_devices():
        with connect(db_path) as connection:
            rows = connection.execute(
                """SELECT d.mac, d.extension, d.model, d.server_host, d.server_port,
                          d.transport, d.created_at, e.display_name, e.enabled
                   FROM polycom_devices d JOIN extensions e ON e.extension = d.extension
                   ORDER BY d.mac"""
            ).fetchall()
        return [{
            "mac": row["mac"], "extension": row["extension"],
            "display_name": row["display_name"], "model": row["model"],
            "server_host": row["server_host"], "server_port": row["server_port"],
            "transport": row["transport"], "enabled": bool(row["enabled"]),
            "created_at": row["created_at"],
        } for row in rows]

    @app.post("/api/v1/devices/polycom/{mac}/credentials",
              dependencies=[Depends(require_token)], tags=["Polycom provisioning"])
    def rotate_polycom_credentials(mac: str):
        try:
            canonical_mac = normalize_mac(mac)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        password = secrets.token_urlsafe(32)
        with connect(db_path) as connection:
            cursor = connection.execute(
                "UPDATE polycom_devices SET provisioning_password_hash = ?, created_at = ? WHERE mac = ?",
                (hashlib.sha256(password.encode()).hexdigest(), _utc_now(), canonical_mac),
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Polycom device not found")
        return {
            "mac": canonical_mac,
            "provisioning_username": "tomos-" + canonical_mac.lower(),
            "provisioning_password": password,
            "password_displayed_once": True,
        }

    @app.delete("/api/v1/devices/polycom/{mac}", status_code=status.HTTP_204_NO_CONTENT,
                dependencies=[Depends(require_token)], tags=["Polycom provisioning"])
    def remove_polycom_device(mac: str):
        try:
            canonical_mac = normalize_mac(mac)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        with connect(db_path) as connection:
            cursor = connection.execute("DELETE FROM polycom_devices WHERE mac = ?", (canonical_mac,))
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Polycom device not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/provisioning/polycom/{mac}.cfg", tags=["Polycom provisioning"])
    def polycom_configuration(
        mac: str, request: Request,
        credentials: HTTPBasicCredentials | None = Depends(provisioning_security),
    ):
        """Serve the per-MAC Polycom VVX XML profile over HTTPS with device Basic auth."""
        if request.url.scheme != "https" and request.headers.get("x-forwarded-proto", "").lower() != "https":
            raise HTTPException(status_code=400, detail="Polycom provisioning requires HTTPS")
        try:
            canonical_mac = normalize_mac(mac)
        except ValueError as error:
            raise HTTPException(status_code=404, detail="Provisioning profile not found") from error
        if credentials is None:
            raise HTTPException(status_code=401, detail="Provisioning authentication required",
                                headers={"WWW-Authenticate": "Basic"})
        with connect(db_path) as connection:
            row = connection.execute(
                """SELECT d.extension, d.server_host, d.server_port, d.transport,
                          d.provisioning_password_hash, e.display_name, e.password, e.enabled
                   FROM polycom_devices d JOIN extensions e ON e.extension = d.extension
                   WHERE d.mac = ?""", (canonical_mac,)
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Provisioning profile not found")
        expected_user = "tomos-" + canonical_mac.lower()
        supplied_hash = hashlib.sha256(credentials.password.encode()).hexdigest()
        if (not secrets.compare_digest(credentials.username, expected_user)
                or not secrets.compare_digest(supplied_hash, row["provisioning_password_hash"])):
            raise HTTPException(status_code=401, detail="Invalid provisioning credentials",
                                headers={"WWW-Authenticate": "Basic"})
        if not row["enabled"]:
            raise HTTPException(status_code=410, detail="The assigned SIP extension is disabled")
        try:
            config = render_polycom_cfg(
                mac=canonical_mac, extension=row["extension"], display_name=row["display_name"],
                sip_password=row["password"], server_host=row["server_host"],
                server_port=row["server_port"], transport=row["transport"],
            )
        except ValueError as error:
            raise HTTPException(status_code=500, detail="Unable to render device configuration") from error
        return Response(content=config, media_type="application/xml",
                        headers={"Cache-Control": "no-store, private", "Pragma": "no-cache",
                                 "X-Content-Type-Options": "nosniff"})

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


    @app.post("/api/v1/devices/cisco", status_code=status.HTTP_201_CREATED,
              dependencies=[Depends(require_token)], tags=["Cisco SCCP provisioning"])
    def register_cisco_device(payload: CiscoDeviceCreate):
        """Add a Cisco SCCP handset to inventory; this does not provision or activate it."""
        try:
            mac = normalize_mac(payload.mac)
            host = validate_server_host(payload.server_host)
            model = normalize_cisco_model(payload.model)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        now = _utc_now()
        with connect(db_path) as connection:
            extension_row = connection.execute(
                "SELECT enabled FROM extensions WHERE extension = ?", (payload.extension,)
            ).fetchone()
            if extension_row is None:
                raise HTTPException(status_code=404, detail="Extension not found")
            if not extension_row["enabled"]:
                raise HTTPException(status_code=409, detail="Enable the SIP extension before assigning a Cisco phone")
            try:
                connection.execute(
                    """INSERT INTO cisco_devices (mac, extension, model, server_host, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (mac, payload.extension, model, host, now),
                )
            except sqlite3.IntegrityError as error:
                raise HTTPException(status_code=409, detail="A Cisco device with this MAC address is already registered") from error
        return {
            "mac": mac, "extension": payload.extension, "model": model,
            "server_host": host, "inventory_status": "registered",
            "provisioning_status": "not_configured",
            "hardware_test_status": "not_tested",
        }

    @app.get("/api/v1/devices/cisco", dependencies=[Depends(require_token)],
             tags=["Cisco SCCP provisioning"])
    def list_cisco_devices():
        with connect(db_path) as connection:
            rows = connection.execute(
                """SELECT d.mac, d.extension, d.model, d.server_host, d.created_at,
                          e.display_name, e.enabled
                   FROM cisco_devices d JOIN extensions e ON e.extension = d.extension
                   ORDER BY d.mac"""
            ).fetchall()
        return [{
            "mac": row["mac"], "extension": row["extension"],
            "display_name": row["display_name"], "model": row["model"],
            "server_host": row["server_host"], "enabled": bool(row["enabled"]),
            "created_at": row["created_at"],
            "provisioning_status": "not_configured",
            "hardware_test_status": "not_tested",
        } for row in rows]

    @app.delete("/api/v1/devices/cisco/{mac}", status_code=status.HTTP_204_NO_CONTENT,
                dependencies=[Depends(require_token)], tags=["Cisco SCCP provisioning"])
    def remove_cisco_device(mac: str):
        try:
            canonical_mac = normalize_mac(mac)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        with connect(db_path) as connection:
            cursor = connection.execute("DELETE FROM cisco_devices WHERE mac = ?", (canonical_mac,))
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Cisco device not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/api/v1/devices/yealink", status_code=status.HTTP_201_CREATED,
              dependencies=[Depends(require_token)], tags=["Yealink provisioning"])
    def register_yealink_device(payload: YealinkDeviceCreate):
        """Register a Yealink phone and issue its one-time provisioning credentials."""
        try:
            mac = normalize_mac(payload.mac)
            host = validate_server_host(payload.server_host)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        try:
            model = normalize_model(payload.model)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        username = "tomos-" + mac.lower()
        password = secrets.token_urlsafe(32)
        now = _utc_now()
        with connect(db_path) as connection:
            extension_row = connection.execute(
                "SELECT enabled FROM extensions WHERE extension = ?", (payload.extension,)
            ).fetchone()
            if extension_row is None:
                raise HTTPException(status_code=404, detail="Extension not found")
            if not extension_row["enabled"]:
                raise HTTPException(status_code=409, detail="Enable the SIP extension before provisioning a phone")
            try:
                connection.execute(
                    """INSERT INTO yealink_devices
                       (mac, extension, model, server_host, provisioning_password_hash, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (mac, payload.extension, model, host,
                     hashlib.sha256(password.encode()).hexdigest(), now),
                )
            except sqlite3.IntegrityError as error:
                raise HTTPException(status_code=409, detail="A Yealink device with this MAC address is already registered") from error
        return {
            "mac": mac, "extension": payload.extension, "model": model,
            "provisioning_path": f"/provisioning/yealink/{mac.lower()}.cfg",
            "provisioning_username": username,
            "provisioning_password": password,
            "authentication": "HTTP Basic (use HTTPS)",
            "password_displayed_once": True,
        }

    @app.post("/api/v1/devices/yealink/{mac}/credentials",
              dependencies=[Depends(require_token)], tags=["Yealink provisioning"])
    def rotate_yealink_credentials(mac: str):
        """Revoke the old device secret and issue a new one, displayed only once."""
        try:
            canonical_mac = normalize_mac(mac)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        password = secrets.token_urlsafe(32)
        with connect(db_path) as connection:
            cursor = connection.execute(
                "UPDATE yealink_devices SET provisioning_password_hash = ?, created_at = ? WHERE mac = ?",
                (hashlib.sha256(password.encode()).hexdigest(), _utc_now(), canonical_mac),
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Yealink device not found")
        return {
            "mac": canonical_mac,
            "provisioning_username": "tomos-" + canonical_mac.lower(),
            "provisioning_password": password,
            "password_displayed_once": True,
        }

    @app.get("/api/v1/devices/yealink", dependencies=[Depends(require_token)],
             tags=["Yealink provisioning"])
    def list_yealink_devices():
        with connect(db_path) as connection:
            rows = connection.execute(
                """SELECT d.mac, d.extension, d.model, d.server_host, d.created_at,
                          e.display_name, e.enabled
                   FROM yealink_devices d JOIN extensions e ON e.extension = d.extension
                   ORDER BY d.mac"""
            ).fetchall()
        return [{
            "mac": row["mac"], "extension": row["extension"],
            "display_name": row["display_name"], "model": row["model"],
            "server_host": row["server_host"], "enabled": bool(row["enabled"]),
            "created_at": row["created_at"],
        } for row in rows]

    @app.delete("/api/v1/devices/yealink/{mac}", status_code=status.HTTP_204_NO_CONTENT,
                dependencies=[Depends(require_token)], tags=["Yealink provisioning"])
    def remove_yealink_device(mac: str):
        try:
            canonical_mac = normalize_mac(mac)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        with connect(db_path) as connection:
            cursor = connection.execute("DELETE FROM yealink_devices WHERE mac = ?", (canonical_mac,))
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Yealink device not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/provisioning/yealink/{mac}.cfg", tags=["Yealink provisioning"])
    def yealink_configuration(
        mac: str, request: Request,
        credentials: HTTPBasicCredentials | None = Depends(provisioning_security),
    ):
        """Serve a per-MAC Yealink CFG only to the phone holding its one-time secret."""
        if request.url.scheme != "https" and request.headers.get("x-forwarded-proto", "").lower() != "https":
            raise HTTPException(status_code=400, detail="Yealink provisioning requires HTTPS")
        try:
            canonical_mac = normalize_mac(mac)
        except ValueError as error:
            raise HTTPException(status_code=404, detail="Provisioning profile not found") from error
        if credentials is None:
            raise HTTPException(status_code=401, detail="Provisioning authentication required",
                                headers={"WWW-Authenticate": "Basic"})
        with connect(db_path) as connection:
            row = connection.execute(
                """SELECT d.extension, d.server_host, d.provisioning_password_hash,
                          e.display_name, e.password, e.enabled
                   FROM yealink_devices d JOIN extensions e ON e.extension = d.extension
                   WHERE d.mac = ?""", (canonical_mac,)
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Provisioning profile not found")
        expected_user = "tomos-" + canonical_mac.lower()
        supplied_hash = hashlib.sha256(credentials.password.encode()).hexdigest()
        if (not secrets.compare_digest(credentials.username, expected_user)
                or not secrets.compare_digest(supplied_hash, row["provisioning_password_hash"])):
            raise HTTPException(status_code=401, detail="Invalid provisioning credentials",
                                headers={"WWW-Authenticate": "Basic"})
        if not row["enabled"]:
            raise HTTPException(status_code=410, detail="The assigned SIP extension is disabled")
        try:
            config = render_yealink_cfg(
                extension=row["extension"], display_name=row["display_name"],
                sip_password=row["password"], server_host=row["server_host"],
            )
        except ValueError as error:
            raise HTTPException(status_code=500, detail="Unable to render device configuration") from error
        return PlainTextResponse(config, media_type="text/plain",
                                 headers={"Cache-Control": "no-store, private",
                                          "Pragma": "no-cache",
                                          "X-Content-Type-Options": "nosniff"})

    @app.post("/api/v1/devices/grandstream", status_code=status.HTTP_201_CREATED,
              dependencies=[Depends(require_token)], tags=["Grandstream provisioning"])
    def register_grandstream_device(payload: GrandstreamDeviceCreate):
        """Register a Grandstream GRP260x phone and issue one-time provisioning credentials."""
        try:
            mac = normalize_mac(payload.mac)
            host = validate_server_host(payload.server_host)
            model = normalize_grandstream_model(payload.model)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        username = "tomos-" + mac.lower()
        password = secrets.token_urlsafe(32)
        now = _utc_now()
        with connect(db_path) as connection:
            extension_row = connection.execute(
                "SELECT enabled FROM extensions WHERE extension = ?", (payload.extension,)
            ).fetchone()
            if extension_row is None:
                raise HTTPException(status_code=404, detail="Extension not found")
            if not extension_row["enabled"]:
                raise HTTPException(status_code=409, detail="Enable the SIP extension before provisioning a phone")
            try:
                connection.execute(
                    """INSERT INTO grandstream_devices
                       (mac, extension, model, server_host, provisioning_password_hash, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (mac, payload.extension, model, host,
                     hashlib.sha256(password.encode()).hexdigest(), now),
                )
            except sqlite3.IntegrityError as error:
                raise HTTPException(status_code=409, detail="A Grandstream device with this MAC address is already registered") from error
        return {
            "mac": mac, "extension": payload.extension, "model": model,
            "provisioning_path": f"/provisioning/grandstream/cfg{mac.lower()}.xml",
            "provisioning_username": username,
            "provisioning_password": password,
            "authentication": "HTTP Basic (use HTTPS)",
            "password_displayed_once": True,
        }

    @app.post("/api/v1/devices/grandstream/{mac}/credentials",
              dependencies=[Depends(require_token)], tags=["Grandstream provisioning"])
    def rotate_grandstream_credentials(mac: str):
        try:
            canonical_mac = normalize_mac(mac)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        password = secrets.token_urlsafe(32)
        with connect(db_path) as connection:
            cursor = connection.execute(
                "UPDATE grandstream_devices SET provisioning_password_hash = ?, created_at = ? WHERE mac = ?",
                (hashlib.sha256(password.encode()).hexdigest(), _utc_now(), canonical_mac),
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Grandstream device not found")
        return {
            "mac": canonical_mac,
            "provisioning_username": "tomos-" + canonical_mac.lower(),
            "provisioning_password": password,
            "password_displayed_once": True,
        }

    @app.get("/api/v1/devices/grandstream", dependencies=[Depends(require_token)],
             tags=["Grandstream provisioning"])
    def list_grandstream_devices():
        with connect(db_path) as connection:
            rows = connection.execute(
                """SELECT d.mac, d.extension, d.model, d.server_host, d.created_at,
                          e.display_name, e.enabled
                   FROM grandstream_devices d JOIN extensions e ON e.extension = d.extension
                   ORDER BY d.mac"""
            ).fetchall()
        return [{
            "mac": row["mac"], "extension": row["extension"],
            "display_name": row["display_name"], "model": row["model"],
            "server_host": row["server_host"], "enabled": bool(row["enabled"]),
            "created_at": row["created_at"],
        } for row in rows]

    @app.delete("/api/v1/devices/grandstream/{mac}", status_code=status.HTTP_204_NO_CONTENT,
                dependencies=[Depends(require_token)], tags=["Grandstream provisioning"])
    def remove_grandstream_device(mac: str):
        try:
            canonical_mac = normalize_mac(mac)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        with connect(db_path) as connection:
            cursor = connection.execute("DELETE FROM grandstream_devices WHERE mac = ?", (canonical_mac,))
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Grandstream device not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/provisioning/grandstream/cfg{mac}.xml", tags=["Grandstream provisioning"])
    def grandstream_configuration(
        mac: str, request: Request,
        credentials: HTTPBasicCredentials | None = Depends(provisioning_security),
    ):
        """Serve a per-MAC Grandstream XML profile using per-device HTTPS Basic auth."""
        if request.url.scheme != "https" and request.headers.get("x-forwarded-proto", "").lower() != "https":
            raise HTTPException(status_code=400, detail="Grandstream provisioning requires HTTPS")
        try:
            canonical_mac = normalize_mac(mac)
        except ValueError as error:
            raise HTTPException(status_code=404, detail="Provisioning profile not found") from error
        if credentials is None:
            raise HTTPException(status_code=401, detail="Provisioning authentication required",
                                headers={"WWW-Authenticate": "Basic"})
        with connect(db_path) as connection:
            row = connection.execute(
                """SELECT d.extension, d.server_host, d.provisioning_password_hash,
                          e.display_name, e.password, e.enabled
                   FROM grandstream_devices d JOIN extensions e ON e.extension = d.extension
                   WHERE d.mac = ?""", (canonical_mac,)
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Provisioning profile not found")
        expected_user = "tomos-" + canonical_mac.lower()
        supplied_hash = hashlib.sha256(credentials.password.encode()).hexdigest()
        if (not secrets.compare_digest(credentials.username, expected_user)
                or not secrets.compare_digest(supplied_hash, row["provisioning_password_hash"])):
            raise HTTPException(status_code=401, detail="Invalid provisioning credentials",
                                headers={"WWW-Authenticate": "Basic"})
        if not row["enabled"]:
            raise HTTPException(status_code=410, detail="The assigned SIP extension is disabled")
        try:
            config = render_grandstream_xml(
                mac=canonical_mac, extension=row["extension"], display_name=row["display_name"],
                sip_password=row["password"], server_host=row["server_host"],
            )
        except ValueError as error:
            raise HTTPException(status_code=500, detail="Unable to render device configuration") from error
        return Response(content=config, media_type="application/xml",
                        headers={"Cache-Control": "no-store, private", "Pragma": "no-cache",
                                 "X-Content-Type-Options": "nosniff"})

    @app.get("/api/v1/extensions/{extension}/provisioning", dependencies=[Depends(require_token)], tags=["provisioning"])
    def extension_provisioning(extension: str):
        """Return vendor-neutral SIP account settings without disclosing the SIP secret."""
        if not re.fullmatch(EXTENSION_PATTERN, extension):
            raise HTTPException(status_code=422, detail="Invalid extension number")
        with connect(db_path) as connection:
            row = connection.execute(
                "SELECT extension, display_name, enabled FROM extensions WHERE extension = ?",
                (extension,),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Extension not found")
        return {
            "extension": row["extension"],
            "display_name": row["display_name"],
            "enabled": bool(row["enabled"]),
            "sip_username": row["extension"],
            "authentication_username": row["extension"],
            "server_port": 5060,
            "transport": "UDP",
            "codecs": ["alaw", "ulaw"],
            "outbound_proxy": None,
            "password_included": False,
        }

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
