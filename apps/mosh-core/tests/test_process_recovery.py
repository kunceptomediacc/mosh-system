import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CORE = ROOT / "apps" / "mosh-core"
sys.path.insert(0, str(CORE))

from mosh_core.models import Account, AccountStatus, RunStatus, TaskEnvelope, TaskStatus
from mosh_core.repository import MoshRepository


class ProcessRecoveryTests(unittest.TestCase):
    def test_dead_process_claim_is_recovered(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "mosh.db"
            with MoshRepository(db) as repo:
                repo.register_account(Account("personal", "codex", "personal", "ref", AccountStatus.READY))
                repo.create_task(TaskEnvelope("TASK-P", "MOSH", "owner", "codex", "Crash", provider="codex", account_id="personal"))
                first = repo.enqueue_run("TASK-P")
            code = (
                "from mosh_core.repository import MoshRepository;"
                f"r=MoshRepository(r'{db}');"
                "r.claim_next('dead-worker',lease_seconds=-1);"
                "r.close()"
            )
            env = os.environ.copy()
            env["PYTHONPATH"] = str(CORE)
            completed = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            with MoshRepository(db) as repo:
                replacements = repo.recover_expired_runs()
                self.assertEqual(repo.get_run(first.run_id).status, RunStatus.ABANDONED)
                self.assertEqual(replacements[0].attempt, 2)
                self.assertEqual(repo.get_task("TASK-P").status, TaskStatus.QUEUED)
