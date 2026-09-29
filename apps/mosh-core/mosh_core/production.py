from __future__ import annotations

import hmac
import html
import json
import os
import secrets
from dataclasses import dataclass
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, urlparse

from .api import DashboardAssets, ReadOnlyApi
from .auth_sessions import issue_session, validate_session
from .google_signin import (
    CallbackError,
    MAX_BODY_BYTES,
    credential_failure_code,
    login_page,
    register_claim,
    verify_google_credential,
)
from .repository import MoshRepository


@dataclass(frozen=True, slots=True)
class ProductionSettings:
    database: Path
    public_origin: str
    google_client_id: str
    api_token: str
    write_token: str | None
    port: int

    @classmethod
    def from_environment(cls) -> "ProductionSettings":
        origin = (os.environ.get("MOSH_PUBLIC_ORIGIN") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")
        parsed = urlparse(origin)
        if parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("MOSH_PUBLIC_ORIGIN must be an HTTPS origin without a path")
        if not parsed.hostname.endswith(".onrender.com"):
            raise ValueError("the prepared production profile requires a Render onrender.com origin")
        database_value = os.environ.get("MOSH_DATABASE", "/var/data/mosh.db")
        posix_database = PurePosixPath(database_value)
        if not posix_database.is_absolute() or PurePosixPath("/var/data") not in (posix_database, *posix_database.parents):
            raise ValueError("MOSH_DATABASE must be an absolute path under /var/data")
        database = Path(database_value)
        client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
        if not client_id or len(client_id) > 500:
            raise ValueError("GOOGLE_CLIENT_ID is required and must be bounded")
        api_token = os.environ.get("MOSH_API_TOKEN", "")
        write_token = os.environ.get("MOSH_WRITE_TOKEN") or None
        if len(api_token) < 32 or (write_token is not None and len(write_token) < 32):
            raise ValueError("MOSH tokens must contain at least 32 characters")
        port = int(os.environ.get("PORT", "10000"))
        if not 1 <= port <= 65535:
            raise ValueError("PORT must be between 1 and 65535")
        return cls(database, origin, client_id, api_token, write_token, port)


def _security_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-store",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "X-Frame-Options": "DENY",
    }


def serve(settings: ProductionSettings) -> None:
    settings.database.parent.mkdir(parents=True, exist_ok=True)
    with MoshRepository(settings.database):
        pass
    api = ReadOnlyApi(settings.database, settings.api_token, write_token=settings.write_token)
    dashboard = DashboardAssets(os.environ.get("MOSH_DASHBOARD_ROOT", "/app/apps/mosh-ui"))
    callback_uri = f"{settings.public_origin}/auth/callback"

    class Handler(BaseHTTPRequestHandler):
        def _headers(self, values: dict[str, str] | None = None) -> None:
            for name, value in {**_security_headers(), **(values or {})}.items():
                self.send_header(name, value)

        def _write(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self._headers(headers)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _respond(self) -> None:
            path = urlparse(self.path).path.rstrip("/") or "/"
            if self.command == "GET" and path == "/healthz":
                try:
                    with MoshRepository(settings.database) as repository:
                        healthy = repository.connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
                except Exception:
                    healthy = False
                body = json.dumps({"status": "ok" if healthy else "degraded"}, separators=(",", ":")).encode()
                self._write(200 if healthy else 503, body, {"Content-Type": "application/json; charset=utf-8"})
                return
            if self.command == "GET" and path == "/auth/status":
                cookie = SimpleCookie(self.headers.get("Cookie", ""))
                session = cookie.get("mosh_session")
                self._write(204 if session and validate_session(settings.database, session.value) else 401, b"")
                return
            if self.command == "GET" and path == "/login":
                csrf_token = secrets.token_urlsafe(32)
                nonce = secrets.token_urlsafe(24)
                body = login_page(settings.google_client_id, callback_uri, csrf_token, nonce)
                self._write(200, body, {
                    "Content-Type": "text/html; charset=utf-8",
                    "Set-Cookie": f"mosh_csrf_token={csrf_token}; Path=/; Secure; HttpOnly; SameSite=Strict",
                    "Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}' https://accounts.google.com/gsi/client; frame-src https://accounts.google.com/gsi/; connect-src 'self' https://accounts.google.com/gsi/; style-src 'unsafe-inline'; img-src https://*.gstatic.com data:; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
                })
                return
            if self.command == "POST" and path == "/auth/callback":
                self._google_callback()
                return
            if self.command == "GET":
                asset = dashboard.read(path)
                if asset is not None:
                    encoded, content_type = asset
                    self._write(200, encoded, {
                        "Content-Type": content_type,
                        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'",
                    })
                    return
            raw_body = None
            if self.command == "POST":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = -1
                if 0 <= length <= MAX_BODY_BYTES:
                    raw_body = self.rfile.read(length)
            status, payload, headers = api.dispatch(
                self.command, self.path, self.headers.get("Authorization"),
                self.headers.get("X-MOSH-Write-Token"), raw_body, self.headers.get("Cookie"),
            )
            if "Set-Cookie" in headers and "Secure" not in headers["Set-Cookie"]:
                headers["Set-Cookie"] += "; Secure"
            encoded = json.dumps(payload, separators=(",", ":")).encode()
            self._write(status, encoded, headers)

        def _google_callback(self) -> None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > MAX_BODY_BYTES:
                    raise CallbackError("invalid_callback_size")
                form = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
                credential = form.get("credential", [""])[0]
                form_csrf = form.get("mosh_csrf_token", [""])[0]
                cookie = SimpleCookie(self.headers.get("Cookie", ""))
                cookie_csrf = cookie.get("mosh_csrf_token").value if cookie.get("mosh_csrf_token") else ""
                if not credential or not form_csrf or not cookie_csrf:
                    raise CallbackError("missing_callback_value")
                if not hmac.compare_digest(form_csrf, cookie_csrf):
                    raise CallbackError("csrf_mismatch")
                try:
                    claims = verify_google_credential(credential, settings.google_client_id)
                except Exception as exc:
                    raise CallbackError(credential_failure_code(exc)) from exc
                principal_id, created = register_claim(settings.database, claims)
                session_token, _ = issue_session(settings.database, principal_id)
                message = "Google identity registered" if created else "Google identity verified"
                body = f"<!doctype html><html><body><h1>{message}</h1><p>Opening MOSH...</p></body></html>".encode()
                self._write(303, body, {
                    "Location": settings.public_origin + "/",
                    "Content-Type": "text/html; charset=utf-8",
                    "Set-Cookie": f"mosh_session={session_token}; Path=/; Max-Age=28800; Secure; HttpOnly; SameSite=Strict",
                    "Content-Security-Policy": "default-src 'none'; base-uri 'none'; frame-ancestors 'none'",
                })
            except CallbackError as exc:
                code = html.escape(str(exc))
                body = f"<!doctype html><html><body><h1>Sign-in failed</h1><p>Diagnostic: <code>{code}</code></p><a href='/login'>Try again</a></body></html>".encode()
                self._write(400, body, {"Content-Type": "text/html; charset=utf-8"})
            except Exception:
                body = b"<!doctype html><html><body><h1>Sign-in failed</h1><p>Diagnostic: <code>unexpected_callback_failure</code></p><a href='/login'>Try again</a></body></html>"
                self._write(400, body, {"Content-Type": "text/html; charset=utf-8"})

        do_GET = _respond
        do_POST = _respond
        do_PUT = _respond
        do_PATCH = _respond
        do_DELETE = _respond

        def log_message(self, _format: str, *_args: object) -> None:
            return

    ThreadingHTTPServer(("0.0.0.0", settings.port), Handler).serve_forever()


def main() -> None:
    serve(ProductionSettings.from_environment())


if __name__ == "__main__":
    main()
