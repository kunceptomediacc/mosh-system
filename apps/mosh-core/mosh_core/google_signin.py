from __future__ import annotations

import argparse
import hashlib
import hmac
import html
import secrets
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from .repository import MoshRepository
from .auth_sessions import issue_session, validate_session


MAX_BODY_BYTES = 16 * 1024
MAX_CLOCK_SKEW_SECONDS = 60


class CallbackError(ValueError):
    """A non-sensitive callback failure suitable for local diagnostics."""


def credential_failure_code(exc: Exception) -> str:
    """Map verifier failures to bounded codes without exposing JWT contents."""
    message = str(exc).lower()
    if "audience" in message or "recipient" in message:
        return "credential_invalid_audience"
    if "expired" in message:
        return "credential_expired"
    if "too early" in message or "issued at" in message or "future" in message:
        return "credential_clock_skew"
    if "issuer" in message:
        return "credential_invalid_issuer"
    if "subject" in message:
        return "credential_missing_subject"
    if "signature" in message or "certificate" in message:
        return "credential_signature_failed"
    if "segment" in message or "decode" in message or "malformed" in message:
        return "credential_malformed"
    if "transport" in type(exc).__name__.lower() or "connection" in message or "timed out" in message:
        return "credential_transport_failed"
    return "credential_verification_failed"


def login_page(client_id: str, callback_uri: str, csrf_token: str, script_nonce: str) -> bytes:
    safe_client = html.escape(client_id, quote=True)
    safe_uri = html.escape(callback_uri, quote=True)
    safe_csrf = html.escape(csrf_token, quote=True)
    safe_nonce = html.escape(script_nonce, quote=True)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MOSH Google Sign-In</title><script src="https://accounts.google.com/gsi/client" async defer></script>
<script nonce="{safe_nonce}">function handleCredentialResponse(response){{
const form=document.createElement('form');form.method='POST';form.action='{safe_uri}';
for(const [name,value] of Object.entries({{credential:response.credential,mosh_csrf_token:'{safe_csrf}'}})){{
const input=document.createElement('input');input.type='hidden';input.name=name;input.value=value;form.appendChild(input);}}
document.body.appendChild(form);form.submit();}}
setInterval(async()=>{{try{{const response=await fetch('/auth/status',{{cache:'no-store'}});if(response.ok)location.href='http://127.0.0.1:8889/';}}catch(_error){{}}}},1000);
</script>
<style>body{{margin:0;background:#0b0b0c;color:#f4efe7;font:18px system-ui;display:grid;place-items:center;min-height:100vh}}main{{max-width:580px;padding:48px;border:1px solid #34343a;border-radius:24px}}small{{color:#aaa}}</style></head>
<body><main><h1>Sign in to MOSH</h1><p>Choose either configured Google account. New identities receive no MOSH role until the owner grants one.</p>
<div id="g_id_onload" data-client_id="{safe_client}" data-callback="handleCredentialResponse" data-auto_prompt="false"></div>
<div class="g_id_signin" data-type="standard" data-size="large" data-theme="outline" data-text="sign_in_with" data-shape="rectangular" data-logo_alignment="left"></div>
<p><small>Only OpenID profile and email identity claims are requested. No Google service scopes are requested.</small></p></main></body></html>""".encode("utf-8")


def verify_google_credential(credential: str, client_id: str) -> dict:
    from google.auth.transport import requests
    from google.oauth2 import id_token

    claims = id_token.verify_oauth2_token(
        credential,
        requests.Request(),
        client_id,
        clock_skew_in_seconds=MAX_CLOCK_SKEW_SECONDS,
    )
    if claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
        raise ValueError("invalid Google issuer")
    if claims.get("aud") != client_id or not claims.get("sub"):
        raise ValueError("invalid Google audience or subject")
    if claims.get("email") and not claims.get("email_verified"):
        raise ValueError("Google email is not verified")
    return claims


def register_claim(database: Path, claims: dict) -> tuple[str, bool]:
    subject_sha256 = hashlib.sha256(str(claims["sub"]).encode()).hexdigest()
    alias = str(claims.get("email") or claims.get("name") or "Google account")[:80]
    with MoshRepository(database) as repository:
        existing = repository.connection.execute(
            "SELECT principal_id FROM identity_principals WHERE provider_id='google' AND subject_sha256=?",
            (subject_sha256,),
        ).fetchone()
        if existing:
            return str(existing["principal_id"]), False
        principal = repository.register_identity_principal("google", subject_sha256, alias, "owner")
        return str(principal["principal_id"]), True


def serve(database: Path, client_id: str, host: str = "127.0.0.1", port: int = 1455) -> None:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("Google sign-in listener is loopback-only")
    callback_uri = f"http://{host}:{port}/auth/callback"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/auth/status":
                cookie = SimpleCookie(self.headers.get("Cookie", ""))
                session = cookie.get("mosh_session")
                self.send_response(204 if session and validate_session(database, session.value) else 401)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            if self.path.rstrip("/") not in {"", "/login"}:
                self.send_error(404)
                return
            csrf_token = secrets.token_urlsafe(32)
            script_nonce = secrets.token_urlsafe(24)
            body = login_page(client_id, callback_uri, csrf_token, script_nonce)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Set-Cookie", f"mosh_csrf_token={csrf_token}; Path=/; SameSite=Strict; HttpOnly")
            self.send_header("Content-Security-Policy", f"default-src 'none'; script-src 'nonce-{script_nonce}' https://accounts.google.com/gsi/client; frame-src https://accounts.google.com/gsi/; connect-src https://accounts.google.com/gsi/; style-src 'unsafe-inline'; img-src https://*.gstatic.com data:; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if self.path != "/auth/callback":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > MAX_BODY_BYTES:
                    raise CallbackError("invalid_callback_size")
                form = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
                credential = form.get("credential", [""])[0]
                form_csrf = form.get("mosh_csrf_token", [""])[0]
                cookie = SimpleCookie(self.headers.get("Cookie", ""))
                cookie_csrf = cookie.get("mosh_csrf_token").value if cookie.get("mosh_csrf_token") else ""
                if not credential:
                    raise CallbackError("missing_credential")
                if not form_csrf:
                    raise CallbackError("missing_form_csrf")
                if not cookie_csrf:
                    raise CallbackError("missing_cookie_csrf")
                if not hmac.compare_digest(form_csrf, cookie_csrf):
                    raise CallbackError("csrf_mismatch")
                try:
                    claims = verify_google_credential(credential, client_id)
                except Exception as exc:
                    raise CallbackError(credential_failure_code(exc)) from exc
                try:
                    principal_id, created = register_claim(database, claims)
                except Exception as exc:
                    raise CallbackError("identity_registration_failed") from exc
                message = "Google identity registered" if created else "Google identity verified"
                session_token, _expires_at = issue_session(database, principal_id)
                body = f"<!doctype html><html><body><h1>{message}</h1><p>Opening MOSH...</p></body></html>".encode()
                status = 303
            except CallbackError as exc:
                error_code = html.escape(str(exc))
                body = f"<!doctype html><html><body><h1>Sign-in failed</h1><p>No identity was registered.</p><p>Diagnostic: <code>{error_code}</code></p><a href='/'>Try again</a></body></html>".encode()
                status = 400
            except Exception:
                body = b"<!doctype html><html><body><h1>Sign-in failed</h1><p>No identity was registered.</p><p>Diagnostic: <code>unexpected_callback_failure</code></p><a href='/'>Try again</a></body></html>"
                status = 400
            self.send_response(status)
            if status == 303:
                self.send_header("Location", "http://127.0.0.1:8889/")
                self.send_header("Set-Cookie", f"mosh_session={session_token}; Path=/; Max-Age=28800; HttpOnly; SameSite=Strict")
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    ThreadingHTTPServer((host, port), Handler).serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Loopback-only MOSH Google Sign-In listener")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--client-id-file", required=True, type=Path)
    parser.add_argument("--port", type=int, default=1455)
    args = parser.parse_args()
    client_id = args.client_id_file.read_text(encoding="utf-8").strip()
    serve(args.db, client_id, port=args.port)


if __name__ == "__main__":
    main()
