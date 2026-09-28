from __future__ import annotations

import getpass
import json
import sys
from pathlib import Path
from typing import Callable

from .n8n_adapter import N8nReadOnlyAdapter
from .n8n_discovery import GovernedN8nDiscovery, N8nDiscoveryError, N8nWorkflowDiscoveryAdapter
from .repository import MoshRepository


def authorize_packet_file(
    repository: MoshRepository,
    packet_path: str | Path,
    requested_by: str,
    approved_by: str,
) -> dict:
    """Validate one bounded local packet and persist only its governed digest record."""
    path = Path(packet_path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("n8n discovery packet must be a regular non-symlink file")
    if path.stat().st_size > 8_192:
        raise ValueError("n8n discovery packet exceeds 8192 bytes")
    try:
        packet = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("n8n discovery packet must be valid UTF-8 JSON") from None
    return GovernedN8nDiscovery(repository).authorize(packet, requested_by, approved_by)


def read_api_key_from_tty() -> str:
    """Read a one-shot secret only from an interactive terminal with echo disabled."""
    if not sys.stdin.isatty():
        raise ValueError("n8n credential input requires an interactive terminal")
    value = getpass.getpass("n8n API key (input hidden): ")
    if not value:
        raise ValueError("n8n API key is required")
    return value


def execute_approved_discovery(
    repository: MoshRepository,
    request_id: str,
    credential_reader: Callable[[], str] = read_api_key_from_tty,
    health_factory: Callable[[], N8nReadOnlyAdapter] = N8nReadOnlyAdapter,
    discovery_factory: Callable[..., N8nWorkflowDiscoveryAdapter] = N8nWorkflowDiscoveryAdapter,
) -> dict:
    governance = GovernedN8nDiscovery(repository)
    request = governance.get_authorized_request(request_id)
    if governance.has_outcome(request_id):
        raise ValueError("n8n discovery request already has an immutable outcome")

    try:
        if health_factory().health().get("status") != "healthy":
            return governance.record_failure(request_id, "health_failed")
    except Exception:
        return governance.record_failure(request_id, "health_failed")

    try:
        api_key = credential_reader()
    except (EOFError, KeyboardInterrupt, ValueError):
        return governance.record_failure(request_id, "credential_unavailable")

    try:
        adapter = discovery_factory(max_response_bytes=request["max_response_bytes"])
        result = adapter.discover(api_key, request["max_workflows"])
    except N8nDiscoveryError as error:
        code = str(error).removeprefix("n8n_discovery_")
        mapped = {
            "transport_error": "connector_error",
            "http_error": "connector_error",
            "invalid_json": "invalid_response",
            "invalid_shape": "invalid_response",
            "invalid_item": "invalid_response",
            "item_limit_exceeded": "invalid_response",
        }.get(code, code)
        if mapped not in governance.failure_codes:
            mapped = "connector_error"
        return governance.record_failure(request_id, mapped)
    except Exception:
        return governance.record_failure(request_id, "connector_error")
    finally:
        api_key = None

    return governance.record_success(request_id, result)
