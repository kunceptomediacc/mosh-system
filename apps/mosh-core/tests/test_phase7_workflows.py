import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "mosh-core"))

from mosh_core.repository import MoshRepository


class Phase7WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = MoshRepository(Path(self.temp.name) / "workflows.db")

    def tearDown(self):
        self.repo.close()
        self.temp.cleanup()

    def test_pattern_is_digest_bound_reviewed_and_never_executed(self):
        pattern = self.repo.propose_workflow_pattern(
            "Local verification", "local", "a" * 64, "low", False, "codex",
        )
        self.assertEqual(pattern["status"], "candidate")
        with self.assertRaises(ValueError):
            self.repo.decide_workflow_pattern(pattern["pattern_id"], "approved", "supervisor", "b" * 64)
        approved = self.repo.decide_workflow_pattern(pattern["pattern_id"], "approved", "owner", "b" * 64)
        self.assertEqual(approved["status"], "approved")
        self.assertNotIn("executed", approved)
        with self.assertRaises(ValueError):
            self.repo.decide_workflow_pattern(pattern["pattern_id"], "rejected", "owner", "c" * 64)

    def test_external_effect_pattern_remains_disabled_and_public_status_is_aggregate(self):
        self.repo.propose_workflow_pattern("Private n8n candidate", "n8n", "f" * 64, "high", True, "codex")
        status = self.repo.workflow_status_public()
        self.assertEqual(status["execution"], "disabled")
        self.assertEqual(status["external_effect_patterns"], 1)
        self.assertEqual(status["statuses"]["candidate"], 1)
        encoded = json.dumps(status)
        self.assertNotIn("Private n8n candidate", encoded)
        self.assertNotIn("f" * 64, encoded)

    def test_plans_are_digest_bound_and_external_effects_require_approval(self):
        local = self.repo.propose_workflow_pattern("Local", "local", "1" * 64, "low", False, "codex")
        local = self.repo.decide_workflow_pattern(local["pattern_id"], "approved", "owner", "2" * 64)
        external = self.repo.propose_workflow_pattern("External", "n8n", "3" * 64, "high", True, "codex")
        external = self.repo.decide_workflow_pattern(external["pattern_id"], "approved", "owner", "4" * 64)
        self.assertEqual(self.repo.create_workflow_plan(local["pattern_id"], "5" * 64, "codex")["status"], "ready")
        gated = self.repo.create_workflow_plan(external["pattern_id"], "6" * 64, "codex")
        self.assertEqual(gated["status"], "approval_required")
        self.assertNotIn("execute", gated)
        self.assertEqual(self.repo.workflow_status_public()["plans"], {"ready": 1, "approval_required": 1})

    def test_candidate_pattern_cannot_be_planned(self):
        pattern = self.repo.propose_workflow_pattern("Candidate", "mcp", "7" * 64, "medium", False, "codex")
        with self.assertRaises(ValueError):
            self.repo.create_workflow_plan(pattern["pattern_id"], "8" * 64, "codex")

    def test_automation_tools_are_effect_classified_and_metadata_only(self):
        tool = self.repo.observe_automation_tool("Filesystem read", "mcp", "file.read", "read", "9" * 64, "codex")
        self.assertEqual(tool["status"], "observed")
        with self.assertRaises(ValueError):
            self.repo.decide_automation_tool(tool["tool_id"], "approved", "supervisor", "a" * 64)
        approved = self.repo.decide_automation_tool(tool["tool_id"], "approved", "owner", "a" * 64)
        self.assertEqual(approved["status"], "approved")
        self.assertNotIn("invoke", approved)
        status = self.repo.workflow_status_public()
        self.assertEqual(status["tool_statuses"]["approved"], 1)
        self.assertEqual(status["tool_effects"]["read"], 1)

    def test_write_tool_approval_does_not_enable_execution(self):
        tool = self.repo.observe_automation_tool("External writer", "http", "record.write", "write", "b" * 64, "codex")
        self.repo.decide_automation_tool(tool["tool_id"], "approved", "owner", "c" * 64)
        status = self.repo.workflow_status_public()
        self.assertEqual(status["execution"], "disabled")
        self.assertEqual(status["tool_effects"]["write"], 1)
        self.assertNotIn("External writer", json.dumps(status))


if __name__ == "__main__":
    unittest.main()
