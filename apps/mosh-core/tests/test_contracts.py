import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "mosh-core"))

from mosh_core.models import Account, Agent, Approval, CleanupOperation, CleanupOperationStatus, CleanupPlan, CleanupStatus, Risk, SideEffectExecution, SideEffectExecutionStatus, SideEffectRequest, SideEffectStatus, TaskEnvelope, TaskRun


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.contracts = ROOT / "bridge" / "contracts" / "v1"

    def test_contracts_parse_and_have_no_remote_refs(self):
        for path in self.contracts.glob("*.schema.json"):
            document = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(document["type"], "object")
            self.assertNotIn("$ref", path.read_text(encoding="utf-8"))

    def test_model_fields_match_required_contract_fields(self):
        account = Account("codex-personal", "codex", "personal", "codex-home:personal").to_dict()
        agent = Agent("codex", "Codex", "codex").to_dict()
        task = TaskEnvelope("TASK-1", "MOSH", "owner", "codex", "Test").to_dict()
        run = TaskRun("RUN-1", "TASK-1", 1, "codex-personal").to_dict()
        approval = Approval("APR-1", "TASK-1", "execute", "owner").to_dict()
        side_effect = SideEffectRequest("SFX-1", "TASK-1", "send_email", "a@example.com", {}, Risk.HIGH, "0" * 64, SideEffectStatus.PENDING, "agent").to_dict()
        execution = SideEffectExecution("SFXE-1", "SFX-1", "key", SideEffectExecutionStatus.PREPARED, "marker.txt", "0" * 64).to_dict()
        cleanup = CleanupPlan("CLN-1", "TASK-1", "SFXE-1", "a", "b", "0" * 64, "1" * 64, CleanupStatus.PLANNED).to_dict()
        cleanup_operation = CleanupOperation("CLNO-1", "CLN-1", "SFX-1", "trash", CleanupOperationStatus.PREPARED).to_dict()
        for filename, value in (("account.schema.json", account), ("agent.schema.json", agent), ("task.schema.json", task), ("run.schema.json", run), ("approval.schema.json", approval), ("side-effect-request.schema.json", side_effect), ("side-effect-execution.schema.json", execution), ("cleanup-plan.schema.json", cleanup), ("cleanup-operation.schema.json", cleanup_operation)):
            schema = json.loads((self.contracts / filename).read_text(encoding="utf-8"))
            self.assertTrue(set(schema["required"]).issubset(value))
            self.assertTrue(set(value).issubset(schema["properties"]))

    def test_openapi_is_versioned_and_task_submission_requires_two_tokens(self):
        document = json.loads((self.contracts / "openapi.json").read_text(encoding="utf-8"))
        self.assertEqual(document["openapi"], "3.1.0")
        self.assertEqual(document["security"], [{"bearerAuth": []}, {"sessionCookie": []}])
        self.assertTrue(all(path.startswith("/api/v1/") for path in document["paths"]))
        self.assertEqual(set(document["paths"]["/api/v1/tasks"]), {"get", "post"})
        post_paths = {
            "/api/v1/tasks",
            "/api/v1/sessions/{session_id}/revoke",
            "/api/v1/principals/{principal_id}/sessions/revoke-all",
        }
        self.assertTrue(all(set(operations) == {"get"} for path, operations in document["paths"].items() if path not in post_paths))
        self.assertEqual(
            document["paths"]["/api/v1/tasks"]["post"]["security"],
            [{"bearerAuth": [], "writeToken": []}, {"sessionCookie": [], "writeToken": []}],
        )
        self.assertNotIn("$ref", json.dumps(document))

    def test_github_read_expansion_contract_is_single_call_metadata_only(self):
        schema = json.loads((self.contracts / "github-read-expansion-request.schema.json").read_text(encoding="utf-8"))
        self.assertFalse(schema["additionalProperties"])
        properties = schema["properties"]
        self.assertEqual(properties["agent_id"]["const"], "cline")
        self.assertEqual(properties["operation"]["const"], "repository.metadata")
        self.assertEqual(properties["max_connector_calls"]["const"], 1)
        self.assertFalse(properties["persist_response_content"]["const"])
        self.assertFalse(properties["allow_github_writes"]["const"])
        self.assertEqual(properties["allowlist_mode"]["const"], "expiring")
        self.assertIn("expires_at", schema["required"])
        self.assertEqual(properties["expires_at"]["format"], "date-time")

    def test_n8n_discovery_contract_is_bounded_metadata_only(self):
        schema = json.loads((self.contracts / "n8n-workflow-discovery-request.schema.json").read_text(encoding="utf-8"))
        self.assertFalse(schema["additionalProperties"])
        properties = schema["properties"]
        self.assertEqual(properties["base_url"]["const"], "http://127.0.0.1:5678")
        self.assertEqual(properties["operation"]["const"], "workflow.list_metadata")
        self.assertEqual(properties["max_connector_calls"]["const"], 1)
        self.assertEqual(properties["max_workflows"]["maximum"], 25)
        self.assertEqual(properties["max_response_bytes"]["maximum"], 1_048_576)
        for field in (
            "persist_workflow_definitions", "include_credentials", "include_execution_records",
            "allow_mutation", "allow_activation", "allow_execution", "allow_webhooks",
        ):
            self.assertFalse(properties[field]["const"])


if __name__ == "__main__":
    unittest.main()
