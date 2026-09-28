import json
import sys
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "mosh-core"))

from mosh_core.n8n_discovery import GovernedN8nDiscovery, N8nDiscoveryError, N8nWorkflowDiscoveryAdapter
from mosh_core.repository import MoshRepository


class _Response:
    status = 200

    def __init__(self, payload):
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, limit):
        return self.body[:limit]


class N8nWorkflowDiscoveryTests(unittest.TestCase):
    def test_exact_loopback_base_url_is_required(self):
        for url in ("http://localhost:5678", "https://127.0.0.1:5678", "http://127.0.0.1:5679"):
            with self.assertRaises(ValueError):
                N8nWorkflowDiscoveryAdapter(url)

    @patch("mosh_core.n8n_discovery.urlopen")
    def test_one_call_projects_metadata_and_returns_only_digests_and_counts(self, mocked):
        mocked.return_value = _Response({
            "data": [{
                "id": "private-id", "name": "Private workflow", "active": True,
                "updatedAt": "2026-09-24T01:02:03.000Z", "nodes": [{"credentials": {"secret": "x"}}],
                "connections": {"webhook": "private"}, "settings": {"execution": "private"},
            }],
            "nextCursor": None,
        })
        result = N8nWorkflowDiscoveryAdapter().discover("runtime-secret", 5)
        self.assertEqual(mocked.call_count, 1)
        request = mocked.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.full_url, "http://127.0.0.1:5678/api/v1/workflows?limit=5&excludePinnedData=true")
        self.assertEqual(request.get_header("X-n8n-api-key"), "runtime-secret")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["active"], 1)
        self.assertEqual(result["inactive"], 0)
        encoded = json.dumps(result)
        for forbidden in ("private-id", "Private workflow", "nodes", "credentials", "connections", "webhook", "runtime-secret"):
            self.assertNotIn(forbidden, encoded)

    @patch("mosh_core.n8n_discovery.urlopen")
    def test_pagination_fails_after_exactly_one_call(self, mocked):
        mocked.return_value = _Response({"data": [], "nextCursor": "opaque"})
        with self.assertRaisesRegex(N8nDiscoveryError, "pagination_required"):
            N8nWorkflowDiscoveryAdapter().discover("runtime-secret", 1)
        self.assertEqual(mocked.call_count, 1)

    @patch("mosh_core.n8n_discovery.urlopen", side_effect=RuntimeError("runtime-secret leaked upstream"))
    def test_transport_errors_are_redacted(self, mocked):
        with self.assertRaises(N8nDiscoveryError) as raised:
            N8nWorkflowDiscoveryAdapter().discover("runtime-secret", 1)
        self.assertEqual(str(raised.exception), "n8n_discovery_transport_error")
        self.assertEqual(mocked.call_count, 1)

    @patch("mosh_core.n8n_discovery.urlopen")
    def test_response_byte_ceiling_is_enforced(self, mocked):
        mocked.return_value = _Response({"data": [], "padding": "x" * 100})
        with self.assertRaisesRegex(N8nDiscoveryError, "response_oversized"):
            N8nWorkflowDiscoveryAdapter(max_response_bytes=20).discover("runtime-secret", 1)
        self.assertEqual(mocked.call_count, 1)

    @patch("mosh_core.n8n_discovery.urlopen")
    def test_malformed_allowed_metadata_fails_closed(self, mocked):
        mocked.return_value = _Response({
            "data": [{"id": "id", "name": "name", "active": "yes", "updatedAt": "not-a-date"}],
            "nextCursor": None,
        })
        with self.assertRaisesRegex(N8nDiscoveryError, "invalid_item"):
            N8nWorkflowDiscoveryAdapter().discover("runtime-secret", 1)
        self.assertEqual(mocked.call_count, 1)


class GovernedN8nDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = MoshRepository(Path(self.temporary.name) / "mosh.db")
        self.governance = GovernedN8nDiscovery(self.repo)
        self.packet = {
            "request_version": "1", "base_url": "http://127.0.0.1:5678",
            "operation": "workflow.list_metadata", "purpose": "Inventory readiness",
            "max_connector_calls": 1, "max_workflows": 5, "max_response_bytes": 65536,
            "persist_workflow_definitions": False, "include_credentials": False,
            "include_execution_records": False, "allow_mutation": False,
            "allow_activation": False, "allow_execution": False, "allow_webhooks": False,
        }

    def tearDown(self):
        self.repo.close()
        self.temporary.cleanup()

    def test_authorization_is_owner_bound_closed_and_purpose_digest_only(self):
        with self.assertRaises(ValueError):
            self.governance.authorize(self.packet, "codex", "operator")
        unsafe = dict(self.packet, allow_execution=True)
        with self.assertRaises(ValueError):
            self.governance.authorize(unsafe, "codex", "owner")
        request = self.governance.authorize(self.packet, "codex", "owner")
        self.assertEqual(request["approved_by"], "owner")
        self.assertNotIn("Inventory readiness", json.dumps(request))
        self.assertEqual(self.governance.authorize(self.packet, "codex", "owner")["request_id"], request["request_id"])

    def test_success_receipt_is_digest_only_bounded_and_immutable(self):
        request = self.governance.authorize(self.packet, "codex", "owner")
        result = {
            "count": 2, "active": 1, "inactive": 1,
            "workflow_identity_sha256": ["a" * 64, "b" * 64],
            "response_sha256": "c" * 64,
        }
        outcome = self.governance.record_success(request["request_id"], result)
        self.assertEqual(outcome["status"], "completed")
        encoded = json.dumps(outcome)
        self.assertNotIn("a" * 64, encoded)
        self.assertNotIn("b" * 64, encoded)
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.governance.record_success(request["request_id"], result)

    def test_failure_receipt_is_allowlisted_and_immutable(self):
        request = self.governance.authorize(self.packet, "codex", "owner")
        with self.assertRaises(ValueError):
            self.governance.record_failure(request["request_id"], "secret leaked")
        outcome = self.governance.record_failure(request["request_id"], "unauthorized")
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(outcome["error_code"], "unauthorized")
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.governance.record_failure(request["request_id"], "timeout")

    def test_status_is_aggregate_and_inspection_is_digest_only(self):
        self.assertEqual(self.governance.status()["outcomes"], {
            "pending": 0, "completed": 0, "failed": 0,
        })
        request = self.governance.authorize(self.packet, "codex", "owner")
        self.assertEqual(self.governance.status()["outcomes"]["pending"], 1)
        inspected = self.governance.inspect(request["request_id"])
        self.assertIsNone(inspected["outcome"])
        self.assertNotIn("Inventory readiness", json.dumps(inspected))
        self.governance.record_failure(request["request_id"], "timeout")
        status = self.governance.status()
        self.assertEqual(status["outcomes"], {"pending": 0, "completed": 0, "failed": 1})
        self.assertIsNotNone(status["last_activity_at"])

    def test_schema_migration_is_forward_only_version_29(self):
        version = self.repo.connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
        self.assertEqual(version, 29)
        tables = {row[0] for row in self.repo.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"n8n_discovery_requests", "n8n_discovery_outcomes"} <= tables)


if __name__ == "__main__":
    unittest.main()
