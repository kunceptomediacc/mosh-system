from __future__ import annotations

import json
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class N8nReadOnlyAdapter:
    """Minimal observed n8n surface. It intentionally cannot submit workflows."""

    def __init__(self, base_url: str = "http://127.0.0.1:5678", timeout: float = 3.0):
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("n8n health adapter is restricted to local HTTP loopback")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("n8n base URL must not contain credentials, query, or fragment")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def capabilities(self) -> dict:
        return {
            "supported": ["health"],
            "unsupported": ["workflow.list", "workflow.read", "workflow.execute", "workflow.write"],
            "external_effects": False,
        }

    def health(self) -> dict:
        request = Request(f"{self.base_url}/healthz", method="GET", headers={"Accept": "application/json"})
        with urlopen(request, timeout=self.timeout) as response:
            if response.status != 200:
                raise RuntimeError(f"n8n health returned HTTP {response.status}")
            body = response.read(1024)
        payload = json.loads(body.decode("utf-8"))
        return {"status": "healthy" if payload.get("status") == "ok" else "degraded", "endpoint": "loopback"}
