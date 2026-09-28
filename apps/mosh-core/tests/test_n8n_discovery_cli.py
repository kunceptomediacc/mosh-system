import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "mosh-core"))

from mosh_core.repository import MoshRepository


class N8nDiscoveryCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "mosh.db"
        self.packet = self.root / "request.json"
        self.value = {
            "request_version": "1", "base_url": "http://127.0.0.1:5678",
            "operation": "workflow.list_metadata", "purpose": "Review local automation inventory",
            "max_connector_calls": 1, "max_workflows": 10, "max_response_bytes": 65536,
            "persist_workflow_definitions": False, "include_credentials": False,
            "include_execution_records": False, "allow_mutation": False,
            "allow_activation": False, "allow_execution": False, "allow_webhooks": False,
        }
        self.packet.write_text(json.dumps(self.value), encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def run_cli(self, *args: str, expected: int = 0) -> dict:
        completed = subprocess.run(
            [sys.executable, "-m", "mosh_core.cli", "--db", str(self.database), *args],
            cwd=ROOT / "apps" / "mosh-core", capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(completed.returncode, expected, completed.stderr or completed.stdout)
        return json.loads(completed.stdout)

    def test_authorization_cli_stores_only_digests_and_performs_no_discovery(self):
        result = self.run_cli(
            "n8n-authorize-discovery", "--packet", str(self.packet),
            "--requested-by", "codex", "--approved-by", "owner",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["max_workflows"], 10)
        encoded = json.dumps(result)
        self.assertNotIn(self.value["purpose"], encoded)
        with MoshRepository(self.database) as repository:
            stored = json.dumps([dict(row) for row in repository.connection.execute(
                "SELECT * FROM n8n_discovery_requests"
            )])
            self.assertNotIn(self.value["purpose"], stored)
            self.assertEqual(repository.connection.execute(
                "SELECT count(*) FROM n8n_discovery_outcomes"
            ).fetchone()[0], 0)

    def test_non_owner_and_unsafe_packet_fail_without_insert(self):
        denied = self.run_cli(
            "n8n-authorize-discovery", "--packet", str(self.packet),
            "--requested-by", "codex", "--approved-by", "operator", expected=2,
        )
        self.assertFalse(denied["ok"])
        self.value["allow_execution"] = True
        self.packet.write_text(json.dumps(self.value), encoding="utf-8")
        unsafe = self.run_cli(
            "n8n-authorize-discovery", "--packet", str(self.packet),
            "--requested-by", "codex", "--approved-by", "owner", expected=2,
        )
        self.assertFalse(unsafe["ok"])
        with MoshRepository(self.database) as repository:
            self.assertEqual(repository.connection.execute(
                "SELECT count(*) FROM n8n_discovery_requests"
            ).fetchone()[0], 0)

    def test_status_is_read_only_aggregate_or_digest_only_detail(self):
        empty = self.run_cli("n8n-discovery-status")
        self.assertEqual(empty["data"]["outcomes"], {"pending": 0, "completed": 0, "failed": 0})
        authorized = self.run_cli(
            "n8n-authorize-discovery", "--packet", str(self.packet),
            "--requested-by", "codex", "--approved-by", "owner",
        )
        aggregate = self.run_cli("n8n-discovery-status")
        self.assertEqual(aggregate["data"]["outcomes"], {"pending": 1, "completed": 0, "failed": 0})
        detail = self.run_cli(
            "n8n-discovery-status", "--request-id", authorized["data"]["request_id"]
        )
        self.assertIsNone(detail["data"]["outcome"])
        encoded = json.dumps(detail)
        self.assertNotIn(self.value["purpose"], encoded)
        self.assertNotIn("response_body", encoded)


if __name__ == "__main__":
    unittest.main()
