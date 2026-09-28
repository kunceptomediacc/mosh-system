from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "mosh-core"))

from mosh_core.api import DashboardAssets, ReadOnlyApi
from mosh_core.auth_sessions import issue_session, revoke_session
from mosh_core.repository import MoshRepository


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an isolated MOSH browser-session QA fixture.")
    parser.add_argument("--port", type=int, default=8892)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="mosh-browser-qa-") as temporary:
        database = Path(temporary) / "mosh.db"
        with MoshRepository(database) as repository:
            repository.register_identity_provider(
                "local", "local_dev", "mosh://local-development", None, "owner"
            )
            principal = repository.register_identity_principal(
                "local", "f" * 64, "Browser QA Owner", "owner"
            )
            repository.grant_workspace_role(principal["principal_id"], "owner", "owner")

        active_token, _ = issue_session(database, principal["principal_id"])
        expired_token, _ = issue_session(database, principal["principal_id"])
        revoke_session(database, expired_token)
        api = ReadOnlyApi(database, token="browser-qa-recovery-token")
        dashboard = DashboardAssets(ROOT / "apps" / "mosh-ui")

        class Handler(BaseHTTPRequestHandler):
            def _respond(self) -> None:
                path = urlparse(self.path).path.rstrip("/") or "/"
                if self.command == "GET" and path in {"/qa/active", "/qa/expired"}:
                    token = active_token if path == "/qa/active" else expired_token
                    self.send_response(302)
                    self.send_header("Location", "/")
                    self.send_header("Set-Cookie", f"mosh_session={token}; Path=/; HttpOnly; SameSite=Strict")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    return
                if self.command == "GET":
                    asset = dashboard.read(path)
                    if asset is not None:
                        encoded, content_type = asset
                        self.send_response(200)
                        self.send_header("Content-Type", content_type)
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("X-Content-Type-Options", "nosniff")
                        self.send_header(
                            "Content-Security-Policy",
                            "default-src 'self'; script-src 'self'; style-src 'self'; "
                            "connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'",
                        )
                        self.send_header("Content-Length", str(len(encoded)))
                        self.end_headers()
                        self.wfile.write(encoded)
                        return
                raw_body = None
                if self.command == "POST":
                    length = int(self.headers.get("Content-Length", "0"))
                    if 0 <= length <= 16_384:
                        raw_body = self.rfile.read(length)
                status, body, headers = api.dispatch(
                    self.command,
                    self.path,
                    self.headers.get("Authorization"),
                    self.headers.get("X-MOSH-Write-Token"),
                    raw_body,
                    self.headers.get("Cookie"),
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

            def log_message(self, format: str, *args: object) -> None:
                return

        print(json.dumps({"ok": True, "url": f"http://localhost:{args.port}/qa/active", "process_id": os.getpid()}), flush=True)
        ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
