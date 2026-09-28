from __future__ import annotations

import hmac
import json
import os
import secrets
import mimetypes
import threading
import time
from collections import deque
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from http.cookies import SimpleCookie

from .repository import MoshRepository
from .models import Risk, TaskEnvelope
from .workspace_status import workspace_status
from .integration_status import integration_status
from .auth_sessions import (
    list_active_sessions,
    revoke_all_sessions_for_principal,
    revoke_session,
    revoke_session_by_id,
    session_identity,
    validate_session,
)


class TokenFile:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def read(self) -> str:
        if not self.path.is_file():
            raise ValueError(f"API token file not found: {self.path}")
        token = self.path.read_text(encoding="utf-8").strip()
        if len(token) < 16:
            raise ValueError("API bearer token must be at least 16 characters")
        return token

    def create(self, rotate: bool = False) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(32)
        if not rotate:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(token + "\n")
            return
        if not self.path.is_file():
            raise ValueError(f"API token file not found: {self.path}")
        temporary = self.path.with_name(f".{self.path.name}.{secrets.token_hex(8)}.tmp")
        try:
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(token + "\n")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


class DashboardAssets:
    ROUTES = {"/": "index.html", "/dashboard": "index.html", "/app.js": "app.js", "/styles.css": "styles.css",
              "/manifest.webmanifest": "manifest.webmanifest", "/sw.js": "sw.js", "/icon.svg": "icon.svg"}

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root is not None else Path(__file__).resolve().parents[2] / "mosh-ui"

    def read(self, path: str) -> tuple[bytes, str] | None:
        filename = self.ROUTES.get(path)
        if filename is None:
            return None
        asset = self.root / filename
        if not asset.is_file():
            return None
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        if filename.endswith((".html", ".js", ".css", ".webmanifest", ".svg")):
            content_type += "; charset=utf-8"
        return asset.read_bytes(), content_type


class ReadOnlyApi:
    def __init__(self, database: str | Path, token: str | None = None, token_file: str | Path | None = None,
                 write_token: str | None = None, write_token_file: str | Path | None = None,
                 workspace_root: str | Path | None = None):
        if (token is None) == (token_file is None):
            raise ValueError("exactly one API token source is required")
        if token is not None and len(token) < 16:
            raise ValueError("API bearer token must be at least 16 characters")
        self.database = Path(database)
        self.token = token
        self.token_file = TokenFile(token_file) if token_file is not None else None
        if write_token is not None and write_token_file is not None:
            raise ValueError("at most one write token source is allowed")
        if write_token is not None and len(write_token) < 16:
            raise ValueError("API write token must be at least 16 characters")
        self.write_token = write_token
        self.write_token_file = TokenFile(write_token_file) if write_token_file is not None else None
        self.workspace_root = Path(workspace_root).resolve() if workspace_root is not None else Path(__file__).resolve().parents[3]
        self._write_attempts: deque[float] = deque()
        self._write_lock = threading.Lock()

    def _current_token(self) -> str:
        return self.token_file.read() if self.token_file is not None else self.token  # type: ignore[return-value]

    def _current_write_token(self) -> str | None:
        if self.write_token_file is not None:
            return self.write_token_file.read()
        return self.write_token

    def dispatch(self, method: str, raw_path: str, authorization: str | None,
                 write_authorization: str | None = None, body: bytes | None = None,
                 cookie_header: str | None = None) -> tuple[int, dict, dict[str, str]]:
        headers = {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}
        bearer_ok = bool(authorization and authorization.startswith("Bearer ") and
                         hmac.compare_digest(authorization[7:], self._current_token()))
        cookie = SimpleCookie(cookie_header or "")
        session = cookie.get("mosh_session")
        session_ok = bool(session and validate_session(self.database, session.value))
        if not bearer_ok and not session_ok:
            if session is not None:
                headers["Set-Cookie"] = "mosh_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"
                return 401, self._error("session_expired", "Session expired or unavailable; sign in again"), headers
            return 401, self._error("unauthorized", "A valid bearer token is required"), headers
        parsed = urlparse(raw_path)
        path = parsed.path.rstrip("/") or "/"
        actor_identity = session_identity(self.database, session.value) if session_ok and session is not None else None
        actor_roles = actor_identity["roles"] if actor_identity is not None else (["owner"] if bearer_ok else [])
        if method == "GET" and path == "/api/v1/me":
            if session_ok and session is not None:
                if actor_identity is None:
                    return 401, self._error("unauthorized", "Session is no longer active"), headers
                return 200, {"data": {"authentication": "google_session", **actor_identity}}, headers
            return 200, {"data": {"authentication": "local_recovery", "display_alias": "Local recovery token", "roles": ["owner"], "expires_at": None}}, headers
        if path == "/api/v1/sessions":
            if "owner" not in actor_roles:
                return 403, self._error("owner_required", "An owner role is required"), headers
            if method != "GET":
                headers["Allow"] = "GET"
                return 405, self._error("method_not_allowed", "This operation is not available"), headers
            current_token = session.value if session_ok and session is not None else None
            return 200, {"data": list_active_sessions(self.database, current_token)}, headers
        session_prefix = "/api/v1/sessions/"
        session_suffix = "/revoke"
        if path.startswith(session_prefix) and path.endswith(session_suffix):
            if "owner" not in actor_roles:
                return 403, self._error("owner_required", "An owner role is required"), headers
            if method != "POST":
                headers["Allow"] = "POST"
                return 405, self._error("method_not_allowed", "This operation is not available"), headers
            session_id = path[len(session_prefix):-len(session_suffix)]
            if not session_id.startswith("SES-") or len(session_id) != 36:
                return 404, self._error("not_found", "Session not found"), headers
            current_token = session.value if session_ok and session is not None else None
            outcome = revoke_session_by_id(self.database, session_id, current_token)
            if outcome == "current_session":
                return 409, self._error("current_session", "Use logout to revoke the current session"), headers
            if outcome == "not_found":
                return 404, self._error("not_found", "Session not found"), headers
            return 200, {"data": {"session_id": session_id, "revoked": True}}, headers
        principal_prefix = "/api/v1/principals/"
        principal_suffix = "/sessions/revoke-all"
        if path.startswith(principal_prefix) and path.endswith(principal_suffix):
            if "owner" not in actor_roles:
                return 403, self._error("owner_required", "An owner role is required"), headers
            if method != "POST":
                headers["Allow"] = "POST"
                return 405, self._error("method_not_allowed", "This operation is not available"), headers
            principal_id = path[len(principal_prefix):-len(principal_suffix)]
            if not principal_id or "/" in principal_id:
                return 404, self._error("not_found", "Principal not found"), headers
            revoked_count = revoke_all_sessions_for_principal(self.database, principal_id)
            if revoked_count is None:
                return 404, self._error("not_found", "Principal not found"), headers
            if actor_identity is not None and actor_identity["principal_id"] == principal_id:
                headers["Set-Cookie"] = "mosh_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"
            return 200, {"data": {"principal_id": principal_id, "revoked_count": revoked_count}}, headers
        if method == "POST" and path == "/api/v1/logout":
            if not session_ok or session is None:
                return 400, self._error("session_required", "A Google session is required"), headers
            revoke_session(self.database, session.value)
            headers["Set-Cookie"] = "mosh_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"
            return 200, {"data": {"signed_out": True}}, headers
        if method == "POST" and path == "/api/v1/tasks" and self._current_write_token() is not None:
            return self._submit_task(write_authorization, body, headers)
        if method != "GET":
            headers["Allow"] = "GET" if self._current_write_token() is None or path != "/api/v1/tasks" else "GET, POST"
            return 405, self._error("method_not_allowed", "This operation is not available"), headers
        try:
            with MoshRepository(self.database) as repository:
                if path == "/api/v1/health":
                    return 200, {"data": {"status": "ok", "version": "v1", "read_only": self._current_write_token() is None}}, headers
                if path == "/api/v1/summary":
                    return 200, {"data": repository.summary()}, headers
                if path == "/api/v1/accounts":
                    return 200, {"data": repository.list_accounts_public()}, headers
                if path == "/api/v1/agents":
                    return 200, {"data": repository.list_agents_public()}, headers
                if path == "/api/v1/projects":
                    query = parse_qs(parsed.query)
                    limit = int(query.get("limit", ["50"])[0])
                    offset = int(query.get("offset", ["0"])[0])
                    projects, total = repository.list_projects_public(limit, offset)
                    return 200, {"data": projects, "meta": {"total": total, "limit": limit, "offset": offset}}, headers
                if path == "/api/v1/workspace":
                    return 200, {"data": workspace_status(self.workspace_root)}, headers
                if path == "/api/v1/integrations":
                    return 200, {"data": integration_status(self.workspace_root, repository.list_agents_public())}, headers
                if path == "/api/v1/plugins":
                    return 200, {"data": repository.list_plugins_public()}, headers
                if path == "/api/v1/plugin-activity":
                    return 200, {"data": repository.plugin_activity_public()}, headers
                if path == "/api/v1/governance-status":
                    return 200, {"data": repository.governance_status_public()}, headers
                if path == "/api/v1/workflow-status":
                    return 200, {"data": repository.workflow_status_public()}, headers
                if path == "/api/v1/video-status":
                    return 200, {"data": repository.video_status_public()}, headers
                if path == "/api/v1/identity-status":
                    return 200, {"data": repository.identity_status_public()}, headers
                if path == "/api/v1/memory-stores":
                    return 200, {"data": repository.memory_status_public()}, headers
                if path == "/api/v1/log-streams":
                    return 200, {"data": repository.log_status_public()}, headers
                if path == "/api/v1/system-status":
                    return 200, {"data": repository.system_status_public()}, headers
                if path == "/api/v1/learning-status":
                    return 200, {"data": repository.learning_status_public()}, headers
                if path == "/api/v1/approvals":
                    query = parse_qs(parsed.query)
                    limit = int(query.get("limit", ["50"])[0])
                    offset = int(query.get("offset", ["0"])[0])
                    decision = query.get("decision", [None])[0]
                    approvals, total = repository.list_approvals_page(limit, offset, decision)
                    return 200, {"data": [item.to_dict() for item in approvals],
                                 "meta": {"total": total, "limit": limit, "offset": offset}}, headers
                if path == "/api/v1/side-effects":
                    query = parse_qs(parsed.query)
                    limit = int(query.get("limit", ["50"])[0])
                    offset = int(query.get("offset", ["0"])[0])
                    status = query.get("status", [None])[0]
                    requests, total = repository.list_side_effects_page(limit, offset, status)
                    return 200, {"data": requests, "meta": {"total": total, "limit": limit, "offset": offset}}, headers
                if path == "/api/v1/tasks":
                    query = parse_qs(parsed.query)
                    limit = int(query.get("limit", ["50"])[0])
                    offset = int(query.get("offset", ["0"])[0])
                    tasks, total = repository.list_tasks(limit, offset)
                    return 200, {"data": [task.to_dict() for task in tasks],
                                 "meta": {"total": total, "limit": limit, "offset": offset}}, headers
                prefix = "/api/v1/tasks/"
                if path.startswith(prefix) and len(path) > len(prefix):
                    remainder = path[len(prefix):]
                    if remainder.endswith("/events"):
                        task_id = remainder[:-len("/events")]
                        if not task_id or repository.get_task(task_id) is None:
                            return 404, self._error("not_found", "Task not found"), headers
                        query = parse_qs(parsed.query)
                        limit = int(query.get("limit", ["50"])[0])
                        after_event_id = int(query.get("after_event_id", ["0"])[0])
                        events, has_next, next_cursor = repository.list_events_page(task_id, after_event_id, limit)
                        return 200, {"data": events, "meta": {"limit": limit, "after_event_id": after_event_id,
                                                               "has_next": has_next, "next_cursor": next_cursor}}, headers
                    snapshot = repository.task_snapshot(remainder)
                    if snapshot is None:
                        return 404, self._error("not_found", "Task not found"), headers
                    return 200, {"data": snapshot}, headers
                if path == "/api/v1/cleanup-plans":
                    return 200, {"data": [plan.to_dict() for plan in repository.list_cleanup_plans()]}, headers
        except (ValueError, TypeError):
            return 400, self._error("invalid_request", "Invalid query parameters"), headers
        return 404, self._error("not_found", "Resource not found"), headers

    def _submit_task(self, write_authorization: str | None, raw_body: bytes | None,
                     headers: dict[str, str]) -> tuple[int, dict, dict[str, str]]:
        expected = self._current_write_token()
        if expected is None or not write_authorization or not hmac.compare_digest(write_authorization, expected):
            return 403, self._error("write_token_required", "A valid write token is required"), headers
        now = time.monotonic()
        with self._write_lock:
            while self._write_attempts and self._write_attempts[0] <= now - 60:
                self._write_attempts.popleft()
            if len(self._write_attempts) >= 10:
                limited_headers = dict(headers)
                limited_headers["Retry-After"] = "60"
                return 429, self._error("rate_limit_exceeded", "Task submission limit reached; try again later"), limited_headers
            self._write_attempts.append(now)
        if raw_body is None or len(raw_body) > 16_384:
            return 400, self._error("invalid_json", "A bounded JSON request body is required"), headers
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return 400, self._error("invalid_json", "Request body must be valid JSON"), headers
        if not isinstance(payload, dict):
            return 400, self._error("invalid_json", "Request body must be a JSON object"), headers
        allowed = {"objective", "account_id", "risk"}
        if set(payload) - allowed:
            return 422, self._error("validation_error", "Only objective, account_id, and risk are accepted"), headers
        objective = payload.get("objective")
        account_id = payload.get("account_id")
        risk_value = payload.get("risk", Risk.LOW.value)
        if not isinstance(objective, str) or not 1 <= len(objective.strip()) <= 4_000:
            return 422, self._error("validation_error", "Objective must contain 1 to 4000 characters"), headers
        if not isinstance(account_id, str) or not account_id or len(account_id) > 200:
            return 422, self._error("validation_error", "A valid account_id is required"), headers
        try:
            risk = Risk(risk_value)
        except (TypeError, ValueError):
            return 422, self._error("validation_error", "Risk must be low, medium, or high"), headers
        try:
            with MoshRepository(self.database) as repository:
                account = repository.connection.execute(
                    "SELECT provider FROM accounts WHERE account_id=? AND status='ready'", (account_id,)
                ).fetchone()
                if account is None:
                    return 422, self._error("validation_error", "Selected account is not ready or does not exist"), headers
                stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
                task_id = f"TASK-UI-{stamp}-{secrets.token_hex(3).upper()}"
                task = repository.create_task(TaskEnvelope(
                    task_id=task_id,
                    project_id="MOSH-INBOX",
                    from_actor="owner",
                    to=account["provider"],
                    provider=account["provider"],
                    account_id=account_id,
                    objective=objective.strip(),
                    acceptance=("Return a reviewable result artifact",),
                    constraints=("Submitted through the MOSH dashboard", "Execution requires owner approval"),
                    risk=risk,
                    approval_required=True,
                ))
                run = repository.enqueue_run(task.task_id)
                task = repository.get_task(task.task_id)  # type: ignore[assignment]
        except ValueError:
            return 422, self._error("validation_error", "Task submission is not valid"), headers
        headers = dict(headers)
        headers["Location"] = f"/api/v1/tasks/{task.task_id}"
        return 201, {"data": {"task": repository_task_public(task), "run": run.to_dict()}}, headers

    @staticmethod
    def _error(code: str, message: str) -> dict:
        return {"error": {"code": code, "message": message}}


def serve(database: str | Path, token: str | None, port: int, token_file: str | Path | None = None,
          dashboard_root: str | Path | None = None, write_token_file: str | Path | None = None) -> None:
    api = ReadOnlyApi(database, token, token_file, write_token_file=write_token_file)
    dashboard = DashboardAssets(dashboard_root)

    class Handler(BaseHTTPRequestHandler):
        def _respond(self) -> None:
            path = urlparse(self.path).path.rstrip("/") or "/"
            if self.command == "GET":
                asset = dashboard.read(path)
                if asset is not None:
                    encoded, content_type = asset
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                    return
            raw_body = None
            if self.command == "POST":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = -1
                if 0 <= length <= 16_384:
                    raw_body = self.rfile.read(length)
            status, body, headers = api.dispatch(
                self.command, self.path, self.headers.get("Authorization"),
                self.headers.get("X-MOSH-Write-Token"), raw_body, self.headers.get("Cookie"),
            )
            encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        do_GET = _respond
        do_POST = _respond
        do_PUT = _respond
        do_PATCH = _respond
        do_DELETE = _respond

        def log_message(self, format: str, *args: object) -> None:
            return

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def repository_task_public(task: TaskEnvelope) -> dict:
    return task.to_dict()
