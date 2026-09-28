from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .db import connect, migrate


SESSION_LIFETIME = timedelta(hours=8)


def _now() -> datetime:
    return datetime.now(UTC)


def issue_session(database: str | Path, principal_id: str) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    issued = _now()
    expires = issued + SESSION_LIFETIME
    connection = connect(database)
    try:
        migrate(connection)
        authorized = connection.execute(
            """SELECT 1 FROM identity_principals p
               JOIN workspace_role_bindings r ON r.principal_id=p.principal_id
               WHERE p.principal_id=? AND p.status='active' LIMIT 1""",
            (principal_id,),
        ).fetchone()
        if authorized is None:
            raise ValueError("active workspace role required")
        connection.execute(
            """INSERT INTO identity_sessions
               (session_id,principal_id,token_sha256,created_at,expires_at,revoked_at)
               VALUES(?,?,?,?,?,NULL)""",
            (f"SES-{uuid.uuid4().hex.upper()}", principal_id,
             hashlib.sha256(token.encode()).hexdigest(),
             issued.isoformat().replace("+00:00", "Z"),
             expires.isoformat().replace("+00:00", "Z")),
        )
        connection.commit()
        return token, expires.isoformat().replace("+00:00", "Z")
    finally:
        connection.close()


def validate_session(database: str | Path, token: str) -> bool:
    if len(token) < 32:
        return False
    digest = hashlib.sha256(token.encode()).hexdigest()
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            """SELECT 1 FROM identity_sessions s
               JOIN identity_principals p ON p.principal_id=s.principal_id
               JOIN workspace_role_bindings r ON r.principal_id=p.principal_id
               WHERE s.token_sha256=? AND s.revoked_at IS NULL
                 AND s.expires_at>? AND p.status='active' LIMIT 1""",
            (digest, _now().isoformat().replace("+00:00", "Z")),
        ).fetchone()
        return row is not None
    except sqlite3.OperationalError:
        return False
    finally:
        connection.close()


def revoke_session(database: str | Path, token: str) -> bool:
    if len(token) < 32:
        return False
    digest = hashlib.sha256(token.encode()).hexdigest()
    connection = sqlite3.connect(database)
    try:
        cursor = connection.execute(
            "UPDATE identity_sessions SET revoked_at=? WHERE token_sha256=? AND revoked_at IS NULL",
            (_now().isoformat().replace("+00:00", "Z"), digest),
        )
        connection.commit()
        return cursor.rowcount == 1
    finally:
        connection.close()


def list_active_sessions(database: str | Path, current_token: str | None = None) -> list[dict]:
    current_digest = hashlib.sha256(current_token.encode()).hexdigest() if current_token else None
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """SELECT s.session_id,s.principal_id,p.display_alias,s.token_sha256,
                      s.created_at,s.expires_at
               FROM identity_sessions s
               JOIN identity_principals p ON p.principal_id=s.principal_id
               WHERE s.revoked_at IS NULL AND s.expires_at>? AND p.status='active'
               ORDER BY s.created_at DESC,s.session_id""",
            (_now().isoformat().replace("+00:00", "Z"),),
        ).fetchall()
        return [
            {
                "session_id": row["session_id"],
                "principal_id": row["principal_id"],
                "display_alias": row["display_alias"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "current": current_digest is not None and hmac.compare_digest(row["token_sha256"], current_digest),
            }
            for row in rows
        ]
    finally:
        connection.close()


def revoke_session_by_id(database: str | Path, session_id: str,
                         current_token: str | None = None) -> str:
    current_digest = hashlib.sha256(current_token.encode()).hexdigest() if current_token else None
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            """SELECT token_sha256 FROM identity_sessions
               WHERE session_id=? AND revoked_at IS NULL AND expires_at>?""",
            (session_id, _now().isoformat().replace("+00:00", "Z")),
        ).fetchone()
        if row is None:
            return "not_found"
        if current_digest is not None and hmac.compare_digest(row[0], current_digest):
            return "current_session"
        cursor = connection.execute(
            "UPDATE identity_sessions SET revoked_at=? WHERE session_id=? AND revoked_at IS NULL",
            (_now().isoformat().replace("+00:00", "Z"), session_id),
        )
        connection.commit()
        return "revoked" if cursor.rowcount == 1 else "not_found"
    finally:
        connection.close()


def revoke_all_sessions_for_principal(database: str | Path, principal_id: str) -> int | None:
    connection = sqlite3.connect(database)
    try:
        principal = connection.execute(
            "SELECT 1 FROM identity_principals WHERE principal_id=?",
            (principal_id,),
        ).fetchone()
        if principal is None:
            return None
        now = _now().isoformat().replace("+00:00", "Z")
        cursor = connection.execute(
            """UPDATE identity_sessions SET revoked_at=?
               WHERE principal_id=? AND revoked_at IS NULL AND expires_at>?""",
            (now, principal_id, now),
        )
        connection.commit()
        return cursor.rowcount
    finally:
        connection.close()


def session_identity(database: str | Path, token: str) -> dict | None:
    if len(token) < 32:
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """SELECT p.principal_id,p.display_alias,s.expires_at
               FROM identity_sessions s
               JOIN identity_principals p ON p.principal_id=s.principal_id
               WHERE s.token_sha256=? AND s.revoked_at IS NULL
                 AND s.expires_at>? AND p.status='active'""",
            (digest, _now().isoformat().replace("+00:00", "Z")),
        ).fetchone()
        if row is None:
            return None
        roles = [item[0] for item in connection.execute(
            "SELECT role FROM workspace_role_bindings WHERE principal_id=? ORDER BY role",
            (row["principal_id"],),
        )]
        if not roles:
            return None
        return {"principal_id": row["principal_id"], "display_alias": row["display_alias"],
                "roles": roles, "expires_at": row["expires_at"]}
    finally:
        connection.close()
