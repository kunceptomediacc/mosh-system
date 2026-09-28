import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "mosh-core"))

from mosh_core.models import TaskEnvelope
from mosh_core.repository import MoshRepository


class Phase4CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "cli.db"
        with MoshRepository(self.db) as repo:
            repo.create_task(TaskEnvelope("TASK-CLI-MEM", "MOSH", "owner", "local", "source"))

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *args: str, expected: int = 0) -> dict:
        completed = subprocess.run(
            [sys.executable, "-m", "mosh_core.cli", "--db", str(self.db), *args],
            cwd=ROOT / "apps" / "mosh-core", capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(completed.returncode, expected, completed.stderr or completed.stdout)
        return json.loads(completed.stdout)

    def test_memory_cli_full_explicit_approval_path(self):
        created = self.run_cli(
            "memory-create", "--memory-id", "MEM-CLI-00000001", "--scope", "task",
            "--scope-id", "TASK-CLI-MEM", "--classification", "internal",
            "--summary", "A reviewed CLI memory fact.", "--source-kind", "task", "--source-id", "TASK-CLI-MEM",
        )
        self.assertEqual(created["data"]["status"], "candidate")
        self.run_cli("memory-validate", "--memory-id", "MEM-CLI-00000001", "--method", "test", "--evidence", "TEST-CLI-1")
        requested = self.run_cli("memory-request-approval", "--memory-id", "MEM-CLI-00000001", "--by", "owner")
        decided = self.run_cli(
            "memory-decide", "--approval-id", requested["data"]["approval_id"],
            "--decision", "approved", "--by", "owner", "--reason", "reviewed",
        )
        self.assertEqual(decided["data"]["status"], "approved")
        inspected = self.run_cli("memory-inspect", "--memory-id", "MEM-CLI-00000001")
        self.assertEqual(inspected["data"]["content_sha256"], created["data"]["content_sha256"])
        retrieved = self.run_cli(
            "memory-retrieve", "--scope", "task", "--scope-id", "TASK-CLI-MEM",
            "--classification", "internal", "--by", "owner",
        )
        self.assertEqual([item["memory_id"] for item in retrieved["data"]], ["MEM-CLI-00000001"])
        bound = self.run_cli(
            "task-memory-bind", "--task-id", "TASK-CLI-MEM", "--memory-id", "MEM-CLI-00000001",
            "--by", "owner", "--reason", "explicit task context",
        )
        self.assertEqual(bound["data"]["content_sha256"], created["data"]["content_sha256"])
        listed = self.run_cli("task-memory-list", "--task-id", "TASK-CLI-MEM")
        self.assertEqual(listed["data"][0]["usable"], 1)
        self.assertNotIn("summary", listed["data"][0])
        expired = self.run_cli(
            "memory-expire", "--memory-id", "MEM-CLI-00000001", "--by", "owner", "--reason", "retention ended",
        )
        self.assertEqual(expired["data"]["status"], "expired")
        listed = self.run_cli("task-memory-list", "--task-id", "TASK-CLI-MEM")
        self.assertEqual(listed["data"][0]["usable"], 0)

    def test_skill_cli_full_explicit_approval_path(self):
        created = self.run_cli(
            "skill-create", "--skill-id", "SKILL-CLI-0000001", "--name", "CLI review",
            "--scope", "core", "--risk", "high", "--procedure-ref", "skills/core/cli-review/SKILL.md",
            "--procedure-sha256", "a" * 64, "--evidence", "TASK-CLI-MEM",
        )
        self.assertEqual(created["data"]["status"], "candidate")
        self.run_cli("skill-validate", "--skill-id", "SKILL-CLI-0000001", "--test", "TEST-CLI-SKILL")
        denied = self.run_cli(
            "skill-request-approval", "--skill-id", "SKILL-CLI-0000001", "--by", "supervisor", expected=2,
        )
        self.assertFalse(denied["ok"])
        requested = self.run_cli("skill-request-approval", "--skill-id", "SKILL-CLI-0000001", "--by", "owner")
        decided = self.run_cli(
            "skill-decide", "--approval-id", requested["data"]["approval_id"],
            "--decision", "approved", "--by", "owner", "--reason", "owner reviewed",
        )
        self.assertEqual(decided["data"]["status"], "approved")
        retired = self.run_cli(
            "skill-retire", "--skill-id", "SKILL-CLI-0000001", "--by", "owner", "--reason", "superseded",
        )
        self.assertEqual(retired["data"]["status"], "retired")

    def test_cli_rejection_returns_machine_readable_error_without_insert(self):
        result = self.run_cli(
            "memory-create", "--memory-id", "MEM-CLI-REJECT01", "--scope", "task",
            "--classification", "internal", "--summary", "token=not-allowed-secret-value",
            "--source-kind", "task", "--source-id", "TASK-CLI-MEM", expected=2,
        )
        self.assertEqual(result["ok"], False)
        with MoshRepository(self.db) as repo:
            self.assertEqual(repo.connection.execute("SELECT count(*) FROM memory_records").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
