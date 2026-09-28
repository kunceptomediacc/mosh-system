import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "mosh-core"))

from mosh_core.repository import MoshRepository


class Phase6GovernanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = MoshRepository(Path(self.temp.name) / "governance.db")

    def tearDown(self):
        self.repo.close()
        self.temp.cleanup()

    def test_owner_configures_immutable_isolated_profile_without_compliance_claim(self):
        with self.assertRaises(ValueError):
            self.repo.configure_project_governance("CLIENT-ONE", "business", "confidential", "client-one-2026", "supervisor", True)
        with self.assertRaises(ValueError):
            self.repo.configure_project_governance("CLIENT-DIRECT", "business", "confidential", "client-direct-2026", "owner", True)
        record = self.repo.configure_project_governance(
            "CLIENT-ONE", "business", "confidential", "client-one-2026", "owner",
        )
        self.assertEqual(record["status"], "draft")
        self.assertEqual(record["compliance_claims_prohibited"], 1)
        self.assertNotIn("isolation_key", record)
        requirement = self.repo.add_governance_requirement("CLIENT-ONE", "backup.restore_evidence", True, "owner")
        with self.assertRaises(ValueError):
            self.repo.activate_project_governance("CLIENT-ONE", "owner")
        self.repo.decide_governance_requirement(requirement["requirement_id"], "evidenced", "owner", "a" * 64)
        self.assertEqual(self.repo.activate_project_governance("CLIENT-ONE", "owner")["status"], "active")
        with self.assertRaises(ValueError):
            self.repo.configure_project_governance("CLIENT-ONE", "experiment", "internal", "other-key-2026", "owner")
        event = self.repo.connection.execute("SELECT * FROM project_governance_events").fetchone()
        self.assertEqual(len(event["isolation_sha256"]), 64)
        self.assertNotIn("isolation_key", event.keys())

    def test_required_controls_cannot_be_waived_and_decisions_are_immutable(self):
        self.repo.configure_project_governance("CLIENT-TWO", "business", "restricted", "client-two-2026", "owner")
        required = self.repo.add_governance_requirement("CLIENT-TWO", "security.review", True, "owner")
        optional = self.repo.add_governance_requirement("CLIENT-TWO", "optional.vendor_note", False, "owner")
        with self.assertRaises(ValueError):
            self.repo.decide_governance_requirement(required["requirement_id"], "waived", "owner")
        waived = self.repo.decide_governance_requirement(optional["requirement_id"], "waived", "owner")
        self.assertEqual(waived["status"], "waived")
        with self.assertRaises(ValueError):
            self.repo.decide_governance_requirement(optional["requirement_id"], "evidenced", "owner", "b" * 64)

    def test_public_status_is_aggregate_and_identity_free(self):
        self.repo.configure_project_governance("PRIVATE-PROJECT", "experiment", "internal", "private-zone-2026", "owner")
        status = self.repo.governance_status_public()
        self.assertEqual(status["profiles"], {"experiment": 1, "business": 0})
        self.assertEqual(status["statuses"], {"draft": 1, "active": 0, "blocked": 0})
        self.assertEqual(status["requirements"], {"gap": 0, "evidenced": 0, "waived": 0})
        encoded = json.dumps(status)
        self.assertNotIn("PRIVATE-PROJECT", encoded)
        self.assertNotIn("private-zone", encoded)

    def test_contract_is_closed_and_disclaims_compliance(self):
        schema = json.loads((ROOT / "bridge" / "contracts" / "v1" / "project-governance.schema.json").read_text())
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["compliance_claims_prohibited"], {"const": 1})

    def test_change_request_is_digest_bound_owner_decided_and_non_executing(self):
        self.repo.configure_project_governance("CHANGE-PROJECT", "experiment", "internal", "change-zone-2026", "owner", True)
        change = self.repo.create_change_request(
            "CHANGE-PROJECT", "code", "medium", "a" * 64, "b" * 64, "cline",
        )
        self.assertEqual(change["status"], "pending")
        with self.assertRaises(ValueError):
            self.repo.decide_change_request(change["change_id"], "approved", "supervisor", "c" * 64)
        decided = self.repo.decide_change_request(change["change_id"], "approved", "owner", "c" * 64)
        self.assertEqual(decided["status"], "approved")
        with self.assertRaises(ValueError):
            self.repo.decide_change_request(change["change_id"], "rejected", "owner", "d" * 64)
        decision = self.repo.connection.execute("SELECT * FROM change_decisions").fetchone()
        self.assertEqual(decision["request_sha256"], change["request_sha256"])
        self.assertNotIn("executed", decided)

    def test_change_aggregate_excludes_project_and_digests(self):
        self.repo.configure_project_governance("SECRET-CHANGE", "experiment", "internal", "secret-change-2026", "owner")
        self.repo.create_change_request("SECRET-CHANGE", "configuration", "low", "d" * 64, "e" * 64, "cline")
        status = self.repo.governance_status_public()
        self.assertEqual(status["changes"], {"pending": 1, "approved": 0, "rejected": 0})
        encoded = json.dumps(status)
        self.assertNotIn("SECRET-CHANGE", encoded)
        self.assertNotIn("d" * 64, encoded)

    def test_governance_artifact_is_digest_only_and_owner_reviewed(self):
        self.repo.configure_project_governance("ARTIFACT-PROJECT", "experiment", "internal", "artifact-zone-2026", "owner")
        artifact = self.repo.submit_governance_artifact("ARTIFACT-PROJECT", "threat_model", "a" * 64, "codex")
        self.assertEqual(artifact["status"], "pending")
        with self.assertRaises(ValueError):
            self.repo.decide_governance_artifact(artifact["artifact_id"], "accepted", "supervisor", "b" * 64)
        accepted = self.repo.decide_governance_artifact(artifact["artifact_id"], "accepted", "owner", "b" * 64)
        self.assertEqual(accepted["status"], "accepted")
        with self.assertRaises(ValueError):
            self.repo.decide_governance_artifact(artifact["artifact_id"], "rejected", "owner", "c" * 64)

    def test_artifact_aggregate_excludes_identity_and_digest(self):
        self.repo.configure_project_governance("PRIVATE-ARTIFACT", "experiment", "internal", "artifact-private-2026", "owner")
        self.repo.submit_governance_artifact("PRIVATE-ARTIFACT", "security_review", "f" * 64, "codex")
        status = self.repo.governance_status_public()
        self.assertEqual(status["artifacts"], {"pending": 1, "accepted": 0, "rejected": 0})
        encoded = json.dumps(status)
        self.assertNotIn("PRIVATE-ARTIFACT", encoded)
        self.assertNotIn("f" * 64, encoded)


if __name__ == "__main__":
    unittest.main()
