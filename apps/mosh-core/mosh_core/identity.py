from __future__ import annotations

import base64
import hashlib
import os
import secrets
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode


@dataclass(frozen=True)
class AuthorizationRequest:
    url: str
    state: str
    nonce: str
    code_verifier: str


class IdentityProvider(Protocol):
    provider_id: str

    def authorization_request(self, redirect_uri: str) -> AuthorizationRequest: ...


class LocalDevelopmentIdentity:
    provider_id = "local-development"

    def authenticate(self, alias: str) -> dict:
        if os.environ.get("MOSH_ENV") != "development":
            raise RuntimeError("local identity is disabled outside explicit development mode")
        if not alias.strip() or len(alias.strip()) > 80:
            raise ValueError("local identity alias must be bounded")
        subject = hashlib.sha256(f"local:{alias.strip()}".encode()).hexdigest()
        return {"provider_id": self.provider_id, "subject_sha256": subject, "alias": alias.strip(),
                "development_only": True}


class GoogleSignInAdapter:
    provider_id = "google"
    issuer = "https://accounts.google.com"
    authorization_endpoint = "https://accounts.google.com/o/oauth2/v2/auth"

    def __init__(self, client_id: str):
        if not client_id.strip() or len(client_id) > 512:
            raise ValueError("Google client ID must be bounded")
        self.client_id = client_id

    def authorization_request(self, redirect_uri: str) -> AuthorizationRequest:
        if not redirect_uri.startswith(("http://127.0.0.1:", "http://localhost:", "https://")):
            raise ValueError("redirect URI must be loopback HTTP or HTTPS")
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        query = urlencode({"client_id": self.client_id, "redirect_uri": redirect_uri, "response_type": "code",
                           "scope": "openid profile email", "state": state, "nonce": nonce,
                           "code_challenge": challenge, "code_challenge_method": "S256",
                           "access_type": "offline", "prompt": "select_account"})
        return AuthorizationRequest(f"{self.authorization_endpoint}?{query}", state, nonce, verifier)
