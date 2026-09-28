import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "mosh-core"))

from mosh_core.models import TaskEnvelope
from mosh_core.repository import MoshRepository


class MemoryGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "memory.db"
        self.repo = MoshRepository(self.db)
        self.repo.create_task(TaskEnvelope("TASK-MEMORY", "MOSH", "owner", "local", "governed source"))
        self.repo.create_task(TaskEnvelope("TASK-OTHER", "OTHER", "owner", "local", "other source"))

    def tearDown(self):
        self.repo.close()
        self.temp.cleanup()

    def test_migration_creates_phase4_tables_forward_only(self):
        version = self.repo.connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
        self.assertGreaterEqual(version, 13)
        tables = {row[0] for row in self.repo.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"memory_records", "memory_validations", "memory_approvals", "skill_candidates", "skill_approvals"} <= tables)
        self.assertTrue({"memory_lifecycle_events", "skill_lifecycle_events"} <= tables)
        self.assertIn("task_memory_bindings", tables)

    def approve_memory(self, memory_id, scope="task", scope_id="TASK-MEMORY", classification="internal"):
        memory = self.repo.create_memory_candidate(
            memory_id, scope, scope_id, classification,
            f"Governed fact for {memory_id}.", "task", "TASK-MEMORY",
        )
        self.repo.validate_memory(memory_id, "test", [f"TEST-{memory_id}"])
        approval = self.repo.request_memory_approval(memory_id, "owner")
        return self.repo.decide_memory_approval(approval["approval_id"], "approved", "owner", "reviewed")

    def test_task_binding_is_digest_bound_scoped_metadata_only_and_auditable_after_expiry(self):
        approved = self.approve_memory("MEM-BINDING-0001")
        binding = self.repo.bind_memory_to_task(
            "TASK-MEMORY", approved["memory_id"], "owner", "needed for this task",
        )
        self.assertEqual(binding["content_sha256"], approved["content_sha256"])
        listed = self.repo.list_task_memory_bindings("TASK-MEMORY")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["usable"], 1)
        self.assertNotIn("summary", listed[0])
        self.assertNotIn("source_reference_id", listed[0])
        with self.assertRaises(ValueError):
            self.repo.bind_memory_to_task("TASK-MEMORY", approved["memory_id"], "owner", "duplicate")
        with self.assertRaises(ValueError):
            self.repo.bind_memory_to_task("TASK-OTHER", approved["memory_id"], "owner", "wrong scope")
        self.repo.expire_memory(approved["memory_id"], "owner", "retention ended")
        self.assertEqual(self.repo.list_task_memory_bindings("TASK-MEMORY")[0]["usable"], 0)

    def test_confidential_binding_requires_owner(self):
        approved = self.approve_memory("MEM-BINDING-0002", classification="confidential")
        with self.assertRaises(ValueError):
            self.repo.bind_memory_to_task("TASK-MEMORY", approved["memory_id"], "supervisor", "not owner")
        bound = self.repo.bind_memory_to_task("TASK-MEMORY", approved["memory_id"], "owner", "owner reviewed")
        self.assertEqual(bound["bound_by"], "owner")

    def test_candidate_rejects_secrets_exclusion_and_unapproved_sources_before_insert(self):
        invalid = (
            ("Bearer abcdefghijklmnopqrstuvwxyz", "task", "TASK-MEMORY"),
            ("safe summary", "task", r"D:\balot\thor\forbidden"),
            ("safe summary", "owner", "owner-note"),
            ("safe summary", "task", "TASK-MISSING"),
        )
        for index, (summary, kind, reference) in enumerate(invalid):
            with self.assertRaises(ValueError):
                self.repo.create_memory_candidate(f"MEM-REJECT-{index:08d}", "task", "TASK-MEMORY", "internal", summary, kind, reference)
        self.assertEqual(self.repo.connection.execute("SELECT count(*) FROM memory_records").fetchone()[0], 0)

    def test_memory_lifecycle_requires_validation_and_digest_bound_immutable_approval(self):
        memory = self.repo.create_memory_candidate(
            "MEM-GOVERNED-0001", "task", "TASK-MEMORY", "internal",
            "The adapter result was reviewed successfully.", "task", "TASK-MEMORY",
        )
        self.assertEqual(memory["status"], "candidate")
        with self.assertRaises(ValueError):
            self.repo.request_memory_approval(memory["memory_id"], "owner")
        validated = self.repo.validate_memory(memory["memory_id"], "test", ["TEST-MEMORY-1"])
        self.assertEqual(validated["status"], "validated")
        approval = self.repo.request_memory_approval(memory["memory_id"], "owner")
        approved = self.repo.decide_memory_approval(approval["approval_id"], "approved", "owner", "evidence reviewed")
        self.assertEqual(approved["status"], "approved")
        with self.assertRaises(ValueError):
            self.repo.decide_memory_approval(approval["approval_id"], "rejected", "owner", "cannot rewrite")

    def test_modified_content_cannot_inherit_pending_approval(self):
        memory = self.repo.create_memory_candidate(
            "MEM-GOVERNED-0002", "task", "TASK-MEMORY", "confidential",
            "A bounded operational fact.", "task", "TASK-MEMORY",
        )
        self.repo.validate_memory(memory["memory_id"], "owner_review", ["OWNER-REVIEW-1"])
        approval = self.repo.request_memory_approval(memory["memory_id"], "owner")
        changed = "Modified after approval request."
        self.repo.connection.execute(
            "UPDATE memory_records SET summary=?,content_sha256=? WHERE memory_id=?",
            (changed, hashlib.sha256(changed.encode()).hexdigest(), memory["memory_id"]),
        )
        with self.assertRaises(ValueError):
            self.repo.decide_memory_approval(approval["approval_id"], "approved", "owner", "stale")

    def test_skill_candidate_requires_bounded_path_digest_and_evidence(self):
        digest = "a" * 64
        skill = self.repo.create_skill_candidate(
            "SKILL-GOVERNED-0001", "Review adapter output", "project", "MOSH",
            "medium", "skills/projects/review-adapter/SKILL.md", digest, ["TASK-MEMORY"],
        )
        self.assertEqual(skill["status"], "candidate")
        self.assertEqual(skill["procedure_sha256"], digest)
        for path in ("../secret", "docs/not-a-skill.md", "skills/../credentials.txt"):
            with self.assertRaises(ValueError):
                self.repo.create_skill_candidate(
                    f"SKILL-REJECT-{abs(hash(path)):08d}", "bad", "project", "MOSH",
                    "low", path, digest, ["TASK-MEMORY"],
                )

    def test_skill_lifecycle_is_tested_digest_bound_and_immutable(self):
        skill = self.repo.create_skill_candidate(
            "SKILL-GOVERNED-0002", "Bounded review", "project", "MOSH", "medium",
            "skills/projects/bounded-review/SKILL.md", "b" * 64, ["TASK-MEMORY"],
        )
        with self.assertRaises(ValueError):
            self.repo.request_skill_approval(skill["skill_id"], "supervisor")
        validated = self.repo.validate_skill(skill["skill_id"], ["TEST-SKILL-1"])
        self.assertEqual(validated["status"], "validated")
        approval = self.repo.request_skill_approval(skill["skill_id"], "supervisor")
        approved = self.repo.decide_skill_approval(approval["approval_id"], "approved", "supervisor", "tests passed")
        self.assertEqual(approved["status"], "approved")
        with self.assertRaises(ValueError):
            self.repo.decide_skill_approval(approval["approval_id"], "rejected", "owner", "immutable")

    def test_shared_or_high_risk_skill_requires_owner(self):
        skill = self.repo.create_skill_candidate(
            "SKILL-GOVERNED-0003", "Core safety", "core", None, "high",
            "skills/core/safety/SKILL.md", "c" * 64, ["TASK-MEMORY"],
        )
        self.repo.validate_skill(skill["skill_id"], ["TEST-SAFETY-1"])
        with self.assertRaises(ValueError):
            self.repo.request_skill_approval(skill["skill_id"], "supervisor")
        approval = self.repo.request_skill_approval(skill["skill_id"], "owner")
        with self.assertRaises(ValueError):
            self.repo.decide_skill_approval(approval["approval_id"], "approved", "supervisor", "not owner")
        approved = self.repo.decide_skill_approval(approval["approval_id"], "approved", "owner", "owner reviewed")
        self.assertEqual(approved["status"], "approved")

    def test_modified_skill_cannot_inherit_pending_approval(self):
        skill = self.repo.create_skill_candidate(
            "SKILL-GOVERNED-0004", "Tamper test", "agent", "codex", "low",
            "skills/agents/tamper/SKILL.md", "d" * 64, ["TASK-MEMORY"],
        )
        self.repo.validate_skill(skill["skill_id"], ["TEST-TAMPER-1"])
        approval = self.repo.request_skill_approval(skill["skill_id"], "supervisor")
        self.repo.connection.execute("UPDATE skill_candidates SET procedure_sha256=? WHERE skill_id=?", ("e" * 64, skill["skill_id"]))
        with self.assertRaises(ValueError):
            self.repo.decide_skill_approval(approval["approval_id"], "approved", "supervisor", "stale")

    def test_retrieval_is_exact_scoped_approved_and_owner_gated(self):
        memory = self.repo.create_memory_candidate(
            "MEM-GOVERNED-0005", "task", "TASK-MEMORY", "confidential",
            "Approved confidential fact.", "task", "TASK-MEMORY",
        )
        self.repo.validate_memory(memory["memory_id"], "owner_review", ["OWNER-REVIEW-2"])
        approval = self.repo.request_memory_approval(memory["memory_id"], "owner")
        self.repo.decide_memory_approval(approval["approval_id"], "approved", "owner", "reviewed")
        old = self.repo.create_memory_candidate(
            "MEM-GOVERNED-OLD01", "task", "TASK-MEMORY", "confidential",
            "Approved but time-expired fact.", "task", "TASK-MEMORY", "2000-01-01T00:00:00Z",
        )
        self.repo.validate_memory(old["memory_id"], "owner_review", ["OWNER-REVIEW-OLD"])
        old_approval = self.repo.request_memory_approval(old["memory_id"], "owner")
        self.repo.decide_memory_approval(old_approval["approval_id"], "approved", "owner", "reviewed")
        with self.assertRaises(ValueError):
            self.repo.retrieve_approved_memory("task", "TASK-MEMORY", "confidential", "supervisor")
        results = self.repo.retrieve_approved_memory("task", "TASK-MEMORY", "confidential", "owner")
        self.assertEqual([item["memory_id"] for item in results], [memory["memory_id"]])
        self.assertEqual(self.repo.retrieve_approved_memory("task", "TASK-MISSING", "confidential", "owner"), [])

    def test_expired_memory_and_retired_skill_leave_digest_bound_audit_events(self):
        memory = self.repo.create_memory_candidate(
            "MEM-GOVERNED-0006", "task", "TASK-MEMORY", "internal",
            "Temporary approved fact.", "task", "TASK-MEMORY",
        )
        self.repo.validate_memory(memory["memory_id"], "test", ["TEST-EXPIRE-1"])
        approval = self.repo.request_memory_approval(memory["memory_id"], "owner")
        approved = self.repo.decide_memory_approval(approval["approval_id"], "approved", "owner", "reviewed")
        expired = self.repo.expire_memory(memory["memory_id"], "owner", "retention ended")
        self.assertEqual(expired["status"], "expired")
        self.assertEqual(self.repo.retrieve_approved_memory("task", "TASK-MEMORY", "internal", "owner"), [])
        memory_event = self.repo.connection.execute("SELECT * FROM memory_lifecycle_events").fetchone()
        self.assertEqual(memory_event["content_sha256"], approved["content_sha256"])

        skill = self.repo.create_skill_candidate(
            "SKILL-GOVERNED-0006", "Retire core", "core", None, "high",
            "skills/core/retire/SKILL.md", "f" * 64, ["TASK-MEMORY"],
        )
        self.repo.validate_skill(skill["skill_id"], ["TEST-RETIRE-1"])
        skill_approval = self.repo.request_skill_approval(skill["skill_id"], "owner")
        self.repo.decide_skill_approval(skill_approval["approval_id"], "approved", "owner", "reviewed")
        with self.assertRaises(ValueError):
            self.repo.retire_skill(skill["skill_id"], "supervisor", "not authorized")
        retired = self.repo.retire_skill(skill["skill_id"], "owner", "superseded")
        self.assertEqual(retired["status"], "retired")
        skill_event = self.repo.connection.execute("SELECT * FROM skill_lifecycle_events").fetchone()
        self.assertEqual(skill_event["procedure_sha256"], "f" * 64)


if __name__ == "__main__":
    unittest.main()
