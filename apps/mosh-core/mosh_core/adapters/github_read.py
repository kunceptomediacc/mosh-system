from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ..repository import MoshRepository, utc_now


_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_MAX_LINES = 500
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ERROR_CODES = {"connector_error", "not_found", "unauthorized", "timeout", "invalid_response"}
_MAX_GRANT_LIFETIME = timedelta(days=30)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class GitHubReadRequest:
    operation: str
    repository_full_name: str
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None


class GovernedGitHubReadAdapter:
    """Authorize bounded GitHub reads; connector execution stays outside this gate."""

    plugin_id = "github"

    def __init__(self, repository: MoshRepository, agent_id: str):
        self.repository = repository
        self.agent_id = agent_id

    def allow_repository(self, repository_full_name: str, allowed_by: str,
                         expires_at: str | None = None) -> dict:
        if allowed_by != "owner":
            raise ValueError("repository allowlisting requires owner authorization")
        self._validate_repository(repository_full_name)
        plugin = self.repository.get_plugin_public(self.plugin_id)
        if plugin["status"] != "connected" or plugin["health_status"] != "healthy":
            raise ValueError("GitHub must be connected and healthy")
        normalized_expiry = self._validate_expiry(expires_at)
        now = utc_now()
        existing = self.repository.connection.execute(
            """SELECT * FROM plugin_repository_access_grants
               WHERE plugin_id=? AND repository_full_name=? AND revoked_at IS NULL
                 AND (expires_at IS NULL OR expires_at>?)
               ORDER BY grant_id DESC LIMIT 1""",
            (self.plugin_id, repository_full_name, now),
        ).fetchone()
        if existing is not None:
            return dict(existing)
        cursor = self.repository.connection.execute(
            """INSERT INTO plugin_repository_access_grants(
                   plugin_id,repository_full_name,allowed_by,created_at,expires_at,revoked_at,revoked_by,revocation_reason
               ) VALUES(?,?,?,?,?,NULL,NULL,NULL)""",
            (self.plugin_id, repository_full_name, allowed_by, now, normalized_expiry),
        )
        return dict(self.repository.connection.execute(
            "SELECT * FROM plugin_repository_access_grants WHERE grant_id=?", (cursor.lastrowid,)
        ).fetchone())

    def revoke_repository(self, repository_full_name: str, revoked_by: str, reason: str) -> dict:
        if revoked_by != "owner":
            raise ValueError("repository revocation requires owner authorization")
        self._validate_repository(repository_full_name)
        bounded_reason = reason.strip()
        if not bounded_reason or len(bounded_reason) > 500:
            raise ValueError("repository revocation requires a bounded reason")
        row = self.repository.connection.execute(
            """SELECT grant_id FROM plugin_repository_access_grants
               WHERE plugin_id=? AND repository_full_name=? AND revoked_at IS NULL
               ORDER BY grant_id DESC LIMIT 1""",
            (self.plugin_id, repository_full_name),
        ).fetchone()
        if row is None:
            raise ValueError("active repository grant does not exist")
        now = utc_now()
        self.repository.connection.execute(
            """UPDATE plugin_repository_access_grants
               SET revoked_at=?,revoked_by=?,revocation_reason=?
               WHERE grant_id=? AND revoked_at IS NULL""",
            (now, revoked_by, bounded_reason, row["grant_id"]),
        )
        return dict(self.repository.connection.execute(
            "SELECT * FROM plugin_repository_access_grants WHERE grant_id=?", (row["grant_id"],)
        ).fetchone())

    def authorize(self, request: GitHubReadRequest) -> dict:
        self._validate_repository(request.repository_full_name)
        if request.operation not in {"repository.metadata", "file.read"}:
            raise ValueError("GitHub adapter supports read operations only")
        plugin = self.repository.get_plugin_public(self.plugin_id)
        if self.agent_id not in plugin["allowed_agents"]:
            raise ValueError("agent is not authorized for GitHub")
        required_scope = "metadata:read" if request.operation == "repository.metadata" else "contents:read"
        if required_scope not in plugin["granted_scopes"]:
            raise ValueError("required GitHub read scope is not verified")
        allowed = self.repository.connection.execute(
            """SELECT 1 FROM plugin_repository_access_grants
               WHERE plugin_id=? AND repository_full_name=? AND revoked_at IS NULL
                 AND (expires_at IS NULL OR expires_at>?) LIMIT 1""",
            (self.plugin_id, request.repository_full_name, utc_now()),
        ).fetchone()
        if allowed is None:
            raise ValueError("repository is not allowlisted")
        resource = None
        start_line = end_line = None
        if request.operation == "file.read":
            resource = self._validate_path(request.path)
            start_line = request.start_line if request.start_line is not None else 1
            end_line = request.end_line if request.end_line is not None else min(start_line + 199, _MAX_LINES)
            if start_line < 1 or end_line < start_line or end_line - start_line + 1 > _MAX_LINES:
                raise ValueError("file reads must request 1..500 ordered lines")
        cursor = self.repository.connection.execute(
            """INSERT INTO plugin_read_events(plugin_id,agent_id,operation,repository_sha256,resource_sha256,start_line,end_line,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (self.plugin_id, self.agent_id, request.operation, _digest(request.repository_full_name),
             _digest(resource) if resource else None, start_line, end_line, utc_now()),
        )
        return {
            "audit_event_id": cursor.lastrowid,
            "operation": request.operation,
            "repository_full_name": request.repository_full_name,
            "path": resource,
            "start_line": start_line,
            "end_line": end_line,
        }

    def record_success(self, event_id: int, response_sha256: str, returned_bytes: int,
                       returned_lines: int) -> dict:
        if not _SHA256.fullmatch(response_sha256):
            raise ValueError("success receipt requires a lowercase SHA-256 digest")
        if returned_bytes < 0 or returned_lines < 0 or returned_lines > _MAX_LINES:
            raise ValueError("success receipt counts are outside adapter bounds")
        self._owned_event(event_id)
        try:
            self.repository.connection.execute(
                """INSERT INTO plugin_read_outcomes(event_id,status,response_sha256,returned_bytes,returned_lines,error_code,completed_at)
                   VALUES(?,'completed',?,?,?,NULL,?)""",
                (event_id, response_sha256, returned_bytes, returned_lines, utc_now()),
            )
        except Exception as error:
            if "UNIQUE constraint failed" in str(error):
                raise ValueError("read outcome is immutable") from error
            raise
        return dict(self.repository.connection.execute(
            "SELECT * FROM plugin_read_outcomes WHERE event_id=?", (event_id,)
        ).fetchone())

    def record_failure(self, event_id: int, error_code: str) -> dict:
        if error_code not in _ERROR_CODES:
            raise ValueError("failure receipt requires an allowlisted error code")
        self._owned_event(event_id)
        try:
            self.repository.connection.execute(
                """INSERT INTO plugin_read_outcomes(event_id,status,response_sha256,returned_bytes,returned_lines,error_code,completed_at)
                   VALUES(?,'failed',NULL,NULL,NULL,?,?)""",
                (event_id, error_code, utc_now()),
            )
        except Exception as error:
            if "UNIQUE constraint failed" in str(error):
                raise ValueError("read outcome is immutable") from error
            raise
        return dict(self.repository.connection.execute(
            "SELECT * FROM plugin_read_outcomes WHERE event_id=?", (event_id,)
        ).fetchone())

    def _owned_event(self, event_id: int) -> None:
        row = self.repository.connection.execute(
            "SELECT 1 FROM plugin_read_events WHERE event_id=? AND plugin_id=? AND agent_id=?",
            (event_id, self.plugin_id, self.agent_id),
        ).fetchone()
        if row is None:
            raise ValueError("read event does not belong to this adapter agent")

    @staticmethod
    def _validate_repository(value: str) -> None:
        if not _REPOSITORY.fullmatch(value):
            raise ValueError("repository must use bounded owner/name syntax")

    @staticmethod
    def _validate_expiry(value: str | None) -> str | None:
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("repository grant expiry must be an ISO-8601 timestamp") from error
        if parsed.tzinfo is None:
            raise ValueError("repository grant expiry must include a timezone")
        expiry = parsed.astimezone(UTC)
        now = datetime.now(UTC)
        if expiry <= now or expiry > now + _MAX_GRANT_LIFETIME:
            raise ValueError("repository grant expiry must be within the next 30 days")
        return expiry.isoformat().replace("+00:00", "Z")

    @staticmethod
    def _validate_path(value: str | None) -> str:
        if not value or len(value) > 500 or value.startswith(("/", "\\")) or "\\" in value:
            raise ValueError("file path must be a bounded repository-relative POSIX path")
        parts = value.split("/")
        if any(part in {"", ".", ".."} for part in parts) or "\x00" in value:
            raise ValueError("file path traversal is prohibited")
        return value
