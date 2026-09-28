import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "mosh-core"))

from mosh_core.n8n_discovery import GovernedN8nDiscovery, N8nDiscoveryError
from mosh_core.n8n_discovery_runner import execute_approved_discovery, read_api_key_from_tty
from mosh_core.repository import MoshRepository


class _Health:
    def __init__(self, status="healthy"):
        self.status = status

    def health(self):
        return {"status": self.status, "endpoint": "loopback"}


class _Discovery:
    def __init__(self, capture, **options):
        self.capture = capture
        self.capture["options"] = options

    def discover(self, api_key, max_workflows):
        self.capture["api_key"] = api_key
        self.capture["max_workflows"] = max_workflows
        return {
            "count": 1, "active": 1, "inactive": 0,
            "workflow_identity_sha256": ["a" * 64], "response_sha256": "b" * 64,
        }


class N8nDiscoveryRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = MoshRepository(Path(self.temporary.name) / "mosh.db")
        packet = {
            "request_version": "1", "base_url": "http://127.0.0.1:5678",
            "operation": "workflow.list_metadata", "purpose": "Bounded readiness",
            "max_connector_calls": 1, "max_workflows": 3, "max_response_bytes": 4096,
            "persist_workflow_definitions": False, "include_credentials": False,
            "include_execution_records": False, "allow_mutation": False,
            "allow_activation": False, "allow_execution": False, "allow_webhooks": False,
        }
        self.request = GovernedN8nDiscovery(self.repo).authorize(packet, "codex", "owner")

    def tearDown(self):
        self.repo.close()
        self.temporary.cleanup()

    def test_redirected_credential_input_is_refused_before_prompt(self):
        with patch("mosh_core.n8n_discovery_runner.sys.stdin.isatty", return_value=False), patch(
            "mosh_core.n8n_discovery_runner.getpass.getpass"
        ) as prompt:
            with self.assertRaisesRegex(ValueError, "interactive terminal"):
                read_api_key_from_tty()
        prompt.assert_not_called()

    def test_runner_health_precedes_prompt_and_persists_no_secret(self):
        calls, capture = [], {}

        def health_factory():
            calls.append("health")
            return _Health()

        def credential_reader():
            calls.append("credential")
            return "runtime-secret"

        result = execute_approved_discovery(
            self.repo, self.request["request_id"], credential_reader, health_factory,
            lambda **options: _Discovery(capture, **options),
        )
        self.assertEqual(calls, ["health", "credential"])
        self.assertEqual(capture, {
            "options": {"max_response_bytes": 4096}, "api_key": "runtime-secret", "max_workflows": 3,
        })
        self.assertEqual(result["status"], "completed")
        database_text = json.dumps([
            dict(row) for table in ("n8n_discovery_requests", "n8n_discovery_outcomes")
            for row in self.repo.connection.execute(f"SELECT * FROM {table}")
        ])
        self.assertNotIn("runtime-secret", database_text)
        self.assertNotIn("Bounded readiness", database_text)

    def test_unhealthy_service_never_requests_credential(self):
        prompted = []
        result = execute_approved_discovery(
            self.repo, self.request["request_id"], lambda: prompted.append(True),
            lambda: _Health("degraded"),
        )
        self.assertEqual(prompted, [])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "health_failed")

    def test_bounded_connector_failure_is_recorded_without_secret(self):
        class Failure:
            def __init__(self, **_):
                pass

            def discover(self, _api_key, _max_workflows):
                raise N8nDiscoveryError("n8n_discovery_invalid_json")

        result = execute_approved_discovery(
            self.repo, self.request["request_id"], lambda: "runtime-secret",
            lambda: _Health(), Failure,
        )
        self.assertEqual(result["error_code"], "invalid_response")
        self.assertNotIn("runtime-secret", json.dumps(result))

    def test_unexpected_connector_error_cannot_reach_output(self):
        class Failure:
            def __init__(self, **_):
                pass

            def discover(self, _api_key, _max_workflows):
                raise RuntimeError("runtime-secret and private response")

        result = execute_approved_discovery(
            self.repo, self.request["request_id"], lambda: "runtime-secret",
            lambda: _Health(), Failure,
        )
        self.assertEqual(result["error_code"], "connector_error")
        self.assertNotIn("runtime-secret", json.dumps(result))
        self.assertNotIn("private response", json.dumps(result))

    def test_request_with_outcome_cannot_prompt_or_run_again(self):
        GovernedN8nDiscovery(self.repo).record_failure(self.request["request_id"], "timeout")
        with self.assertRaisesRegex(ValueError, "already has"):
            execute_approved_discovery(
                self.repo, self.request["request_id"], lambda: self.fail("credential requested"),
            )


if __name__ == "__main__":
    unittest.main()
