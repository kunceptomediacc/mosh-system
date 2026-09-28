from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from .repository import MoshRepository, utc_now


class N8nDiscoveryError(RuntimeError):
    """A bounded discovery failure that never includes credentials or response content."""


class N8nWorkflowDiscoveryAdapter:
    """One-call, metadata-only workflow discovery for the pinned n8n public API."""

    _PATH = "/api/v1/workflows"
    _AUTH_HEADER = "X-N8N-API-KEY"
    _ALLOWED_ITEM_FIELDS = frozenset({"id", "name", "active", "updatedAt"})

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:5678",
        timeout: float = 3.0,
        max_response_bytes: int = 262_144,
    ):
        parsed = urlparse(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.port != 5678
            or parsed.path not in {"", "/"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("n8n discovery requires exact loopback base URL http://127.0.0.1:5678")
        if not 1 <= max_response_bytes <= 1_048_576:
            raise ValueError("n8n discovery response ceiling must be between 1 and 1048576 bytes")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes

    def discover(self, api_key: str, max_workflows: int) -> dict:
        if not isinstance(api_key, str) or not api_key or "\r" in api_key or "\n" in api_key:
            raise ValueError("a valid runtime n8n API key is required")
        if not isinstance(max_workflows, int) or isinstance(max_workflows, bool) or not 1 <= max_workflows <= 25:
            raise ValueError("max_workflows must be an integer from 1 through 25")

        query = urlencode({"limit": max_workflows, "excludePinnedData": "true"})
        request = Request(
            f"{self.base_url}{self._PATH}?{query}",
            method="GET",
            headers={"Accept": "application/json", self._AUTH_HEADER: api_key},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise N8nDiscoveryError("n8n_discovery_http_error")
                body = response.read(self.max_response_bytes + 1)
        except N8nDiscoveryError:
            raise
        except Exception:
            raise N8nDiscoveryError("n8n_discovery_transport_error") from None

        if len(body) > self.max_response_bytes:
            raise N8nDiscoveryError("n8n_discovery_response_oversized")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise N8nDiscoveryError("n8n_discovery_invalid_json") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise N8nDiscoveryError("n8n_discovery_invalid_shape")
        next_cursor = payload.get("nextCursor")
        if next_cursor is not None and next_cursor != "":
            raise N8nDiscoveryError("n8n_discovery_pagination_required")
        if len(payload["data"]) > max_workflows:
            raise N8nDiscoveryError("n8n_discovery_item_limit_exceeded")

        projected = [self._project(item) for item in payload["data"]]
        canonical = json.dumps(projected, sort_keys=True, separators=(",", ":")).encode("utf-8")
        identities = [hashlib.sha256(item["id"].encode("utf-8")).hexdigest() for item in projected]
        active = sum(1 for item in projected if item["active"])
        return {
            "count": len(projected),
            "active": active,
            "inactive": len(projected) - active,
            "workflow_identity_sha256": identities,
            "response_sha256": hashlib.sha256(canonical).hexdigest(),
        }

    @classmethod
    def _project(cls, item: object) -> dict:
        if not isinstance(item, dict):
            raise N8nDiscoveryError("n8n_discovery_invalid_item")
        projected = {field: item.get(field) for field in cls._ALLOWED_ITEM_FIELDS}
        if not isinstance(projected["id"], str) or not projected["id"]:
            raise N8nDiscoveryError("n8n_discovery_invalid_item")
        if not isinstance(projected["name"], str):
            raise N8nDiscoveryError("n8n_discovery_invalid_item")
        if not isinstance(projected["active"], bool):
            raise N8nDiscoveryError("n8n_discovery_invalid_item")
        if not isinstance(projected["updatedAt"], str):
            raise N8nDiscoveryError("n8n_discovery_invalid_item")
        try:
            datetime.fromisoformat(projected["updatedAt"].replace("Z", "+00:00"))
        except ValueError:
            raise N8nDiscoveryError("n8n_discovery_invalid_item") from None
        return projected


class GovernedN8nDiscovery:
    """Persist owner authorization and one immutable digest-only outcome."""

    _REQUEST_KEYS = frozenset({
        "request_version", "base_url", "operation", "purpose", "max_connector_calls",
        "max_workflows", "max_response_bytes", "persist_workflow_definitions",
        "include_credentials", "include_execution_records", "allow_mutation",
        "allow_activation", "allow_execution", "allow_webhooks",
    })
    _FAILURE_CODES = frozenset({
        "health_failed", "credential_unavailable", "connector_error", "unauthorized",
        "timeout", "response_oversized", "pagination_required", "invalid_response",
    })

    def __init__(self, repository: MoshRepository):
        self.repository = repository

    @property
    def failure_codes(self) -> frozenset[str]:
        return self._FAILURE_CODES

    def get_authorized_request(self, request_id: str) -> dict:
        return dict(self._request(request_id))

    def has_outcome(self, request_id: str) -> bool:
        self._request(request_id)
        return self.repository.connection.execute(
            "SELECT 1 FROM n8n_discovery_outcomes WHERE request_id=?", (request_id,)
        ).fetchone() is not None

    def status(self) -> dict:
        requests = int(self.repository.connection.execute(
            "SELECT count(*) FROM n8n_discovery_requests"
        ).fetchone()[0])
        completed = int(self.repository.connection.execute(
            "SELECT count(*) FROM n8n_discovery_outcomes WHERE status='completed'"
        ).fetchone()[0])
        failed = int(self.repository.connection.execute(
            "SELECT count(*) FROM n8n_discovery_outcomes WHERE status='failed'"
        ).fetchone()[0])
        last_activity = self.repository.connection.execute(
            """SELECT max(activity_at) FROM (
                   SELECT created_at AS activity_at FROM n8n_discovery_requests
                   UNION ALL
                   SELECT completed_at AS activity_at FROM n8n_discovery_outcomes
               )"""
        ).fetchone()[0]
        return {
            "requests": requests,
            "outcomes": {"pending": requests - completed - failed, "completed": completed, "failed": failed},
            "last_activity_at": last_activity,
            "execution": "owner_gated",
        }

    def inspect(self, request_id: str) -> dict:
        request = dict(self._request(request_id))
        outcome = self.repository.connection.execute(
            "SELECT * FROM n8n_discovery_outcomes WHERE request_id=?", (request_id,)
        ).fetchone()
        return {"request": request, "outcome": dict(outcome) if outcome is not None else None}

    def authorize(self, packet: dict, requested_by: str, approved_by: str) -> dict:
        if approved_by != "owner":
            raise ValueError("n8n discovery requires owner approval")
        self._validate_packet(packet)
        canonical = json.dumps(packet, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        request_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        existing = self.repository.connection.execute(
            "SELECT * FROM n8n_discovery_requests WHERE request_sha256=?", (request_sha256,)
        ).fetchone()
        if existing is not None:
            return dict(existing)
        request_id = f"N8ND-{uuid.uuid4().hex.upper()}"
        self.repository.connection.execute(
            """INSERT INTO n8n_discovery_requests(
                   request_id,request_sha256,purpose_sha256,max_workflows,max_response_bytes,
                   requested_by,approved_by,created_at
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                request_id,
                request_sha256,
                hashlib.sha256(packet["purpose"].encode("utf-8")).hexdigest(),
                packet["max_workflows"],
                packet["max_response_bytes"],
                requested_by,
                approved_by,
                utc_now(),
            ),
        )
        return dict(self.repository.connection.execute(
            "SELECT * FROM n8n_discovery_requests WHERE request_id=?", (request_id,)
        ).fetchone())

    def record_success(self, request_id: str, result: dict) -> dict:
        request = self._request(request_id)
        required = {"count", "active", "inactive", "workflow_identity_sha256", "response_sha256"}
        if set(result) != required:
            raise ValueError("n8n discovery result has an invalid shape")
        count, active, inactive = result["count"], result["active"], result["inactive"]
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in (count, active, inactive)):
            raise ValueError("n8n discovery counts are invalid")
        if count > request["max_workflows"] or active + inactive != count:
            raise ValueError("n8n discovery counts exceed the approved request")
        identities = result["workflow_identity_sha256"]
        if not isinstance(identities, list) or len(identities) != count or any(not self._is_sha256(value) for value in identities):
            raise ValueError("n8n discovery identity digests are invalid")
        if not self._is_sha256(result["response_sha256"]):
            raise ValueError("n8n discovery response digest is invalid")
        identity_set = json.dumps(sorted(identities), separators=(",", ":")).encode("utf-8")
        values = (
            request_id, "completed", count, active, inactive,
            hashlib.sha256(identity_set).hexdigest(), result["response_sha256"], utc_now(),
        )
        self._insert_outcome(
            """INSERT INTO n8n_discovery_outcomes(
                   request_id,status,workflow_count,active_count,inactive_count,
                   workflow_identity_set_sha256,response_sha256,error_code,completed_at
               ) VALUES(?,?,?,?,?,?,?,NULL,?)""",
            values,
        )
        return self._outcome(request_id)

    def record_failure(self, request_id: str, error_code: str) -> dict:
        self._request(request_id)
        if error_code not in self._FAILURE_CODES:
            raise ValueError("n8n discovery failure code is not allowlisted")
        self._insert_outcome(
            """INSERT INTO n8n_discovery_outcomes(
                   request_id,status,workflow_count,active_count,inactive_count,
                   workflow_identity_set_sha256,response_sha256,error_code,completed_at
               ) VALUES(?,'failed',NULL,NULL,NULL,NULL,NULL,?,?)""",
            (request_id, error_code, utc_now()),
        )
        return self._outcome(request_id)

    def _request(self, request_id: str):
        row = self.repository.connection.execute(
            "SELECT * FROM n8n_discovery_requests WHERE request_id=?", (request_id,)
        ).fetchone()
        if row is None:
            raise ValueError("n8n discovery request does not exist")
        return row

    def _outcome(self, request_id: str) -> dict:
        return dict(self.repository.connection.execute(
            "SELECT * FROM n8n_discovery_outcomes WHERE request_id=?", (request_id,)
        ).fetchone())

    def _insert_outcome(self, statement: str, values: tuple) -> None:
        try:
            self.repository.connection.execute(statement, values)
        except Exception as error:
            if "UNIQUE constraint failed" in str(error):
                raise ValueError("n8n discovery outcome is immutable") from error
            raise

    @classmethod
    def _validate_packet(cls, packet: dict) -> None:
        if not isinstance(packet, dict) or set(packet) != cls._REQUEST_KEYS:
            raise ValueError("n8n discovery request has an invalid shape")
        constants = {
            "request_version": "1", "base_url": "http://127.0.0.1:5678",
            "operation": "workflow.list_metadata", "max_connector_calls": 1,
            "persist_workflow_definitions": False, "include_credentials": False,
            "include_execution_records": False, "allow_mutation": False,
            "allow_activation": False, "allow_execution": False, "allow_webhooks": False,
        }
        if any(packet.get(key) != value for key, value in constants.items()):
            raise ValueError("n8n discovery request exceeds the read-only contract")
        purpose = packet.get("purpose")
        if not isinstance(purpose, str) or not purpose or len(purpose) > 500:
            raise ValueError("n8n discovery purpose must contain 1 through 500 characters")
        for key, upper in (("max_workflows", 25), ("max_response_bytes", 1_048_576)):
            value = packet.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= upper:
                raise ValueError(f"n8n discovery {key} is outside its approved bound")

    @staticmethod
    def _is_sha256(value: object) -> bool:
        return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)
