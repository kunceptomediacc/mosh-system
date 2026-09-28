import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "mosh-core"))

from mosh_core.models import Account, AccountStatus, RunStatus, TaskEnvelope, TaskStatus
from mosh_core.adapters.base import ExecutionPlan, objective_reference
from mosh_core.repository import MoshRepository
from mosh_core.worker import Worker


class FakeExecutor:
    def __init__(self, result="done", error=None):
        self.result = result
        self.error = error

    def build_plan(self, objective):
        return ExecutionPlan(
            "fake", ("fake", objective), hashed_arg_indexes=frozenset({1}),
        )

    def execute_plan(self, plan):
        if self.error:
            raise RuntimeError(self.error)
        return f"{self.result}:{plan.argv[-1]}"


class WorkerTests(unittest.TestCase):
    def test_worker_completes_durable_run_to_review(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "db.sqlite"
            with MoshRepository(path) as repo:
                repo.register_account(Account("personal", "codex", "personal", "ref", AccountStatus.READY))
                repo.create_task(TaskEnvelope("TASK-W", "MOSH", "owner", "codex", "smoke", provider="codex", account_id="personal"))
                queued = repo.enqueue_run("TASK-W")
                self.assertTrue(Worker(repo, "worker", {"personal": FakeExecutor()}).run_once())
                self.assertEqual(repo.get_run(queued.run_id).status, RunStatus.COMPLETED)
            with MoshRepository(path) as reopened:
                self.assertEqual(reopened.get_task("TASK-W").status, TaskStatus.REVIEW)
                self.assertEqual(reopened.get_run(queued.run_id).result, "done:smoke")

    def test_worker_renews_lease_during_slow_execution(self):
        class SlowExecutor:
            def build_plan(self, objective):
                return ExecutionPlan("slow", ("slow", objective), hashed_arg_indexes=frozenset({1}))

            def execute_plan(self, plan):
                time.sleep(0.7)
                return "slow-done"

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "db.sqlite"
            with MoshRepository(path) as repo:
                repo.register_account(Account("personal", "codex", "personal", "ref", AccountStatus.READY))
                repo.create_task(TaskEnvelope("TASK-SLOW", "MOSH", "owner", "codex", "slow", provider="codex", account_id="personal"))
                repo.enqueue_run("TASK-SLOW")
                worker = Worker(repo, "worker", {"personal": SlowExecutor()}, lease_seconds=2, renewal_interval=0.2)
                self.assertTrue(worker.run_once())
                renewals = [event for event in repo.list_events("TASK-SLOW") if event["event_type"] == "run.lease_renewed"]
                self.assertGreaterEqual(len(renewals), 2)
                self.assertEqual(repo.get_task("TASK-SLOW").status, TaskStatus.REVIEW)

    def test_worker_discards_result_when_cancelled_during_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "db.sqlite"
            with MoshRepository(path) as repo:
                repo.register_account(Account("personal", "codex", "personal", "ref", AccountStatus.READY))
                repo.create_task(TaskEnvelope("TASK-CANCEL-RUNNING", "MOSH", "owner", "codex", "cancel me", provider="codex", account_id="personal"))
                queued = repo.enqueue_run("TASK-CANCEL-RUNNING")

                class CancellingExecutor:
                    def build_plan(self, objective):
                        return ExecutionPlan("cancelling", ("cancel", objective),
                                             hashed_arg_indexes=frozenset({1}))

                    def execute_plan(self, plan):
                        repo.request_cancel("TASK-CANCEL-RUNNING", "operator requested stop")
                        return "must-not-be-persisted"

                self.assertTrue(Worker(repo, "worker", {"personal": CancellingExecutor()}).run_once())
                self.assertEqual(repo.get_task("TASK-CANCEL-RUNNING").status, TaskStatus.CANCELLED)
                self.assertEqual(repo.get_run(queued.run_id).status, RunStatus.CANCELLED)
                self.assertIsNone(repo.get_run(queued.run_id).result)
                events = repo.list_events("TASK-CANCEL-RUNNING")
                self.assertEqual(sum(event["event_type"] == "task.cancel.requested" for event in events), 1)
                self.assertEqual(sum(event["event_type"] == "run.cancelled" for event in events), 1)

    def test_worker_persists_only_redacted_canonical_command_evidence(self):
        secret = "SECRET-SENTINEL-never-persist"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "db.sqlite"

            class SecretExecutor:
                def build_plan(self, objective):
                    return ExecutionPlan(
                        "secret-adapter", ("tool", "--token", secret, objective),
                        {"API_TOKEN": secret, "SAFE_MODE": "1"},
                        hashed_arg_indexes=frozenset({3}),
                    )

                def execute_plan(self, plan):
                    return "ok"

            with MoshRepository(path) as repo:
                repo.register_account(Account("personal", "test", "personal", "ref", AccountStatus.READY))
                repo.create_task(TaskEnvelope("TASK-EVIDENCE", "MOSH", "owner", "test", secret,
                                              provider="test", account_id="personal"))
                queued = repo.enqueue_run("TASK-EVIDENCE")
                self.assertTrue(Worker(repo, "worker", {"personal": SecretExecutor()}).run_once())
                run = repo.get_run(queued.run_id)
                self.assertEqual(run.adapter_name, "secret-adapter")
                self.assertEqual(run.command_env_keys_json, '["API_TOKEN","SAFE_MODE"]')
                self.assertNotIn(secret, run.command_argv_json)
                events = repo.list_events("TASK-EVIDENCE")
                recorded = [event for event in events if event["event_type"] == "run.command_recorded"]
                self.assertEqual(len(recorded), 1)
                self.assertEqual(recorded[0]["payload"]["command_evidence_sha256"], run.command_sha256)
            database_bytes = path.read_bytes()
            # The objective table necessarily contains the task objective; command evidence must not duplicate it.
            self.assertEqual(database_bytes.count(secret.encode()), 1)

    def test_central_redaction_handles_malicious_inline_secret_and_api_omits_evidence(self):
        secret = "MALICIOUS-SENTINEL"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "db.sqlite"

            class MaliciousExecutor:
                def build_plan(self, objective):
                    # The adapter declares nothing: unknown inline and positional values must default-deny.
                    return ExecutionPlan("malicious", ("tool", f"--auth={secret}", secret, objective),
                                         hashed_arg_indexes=frozenset({3}))

                def execute_plan(self, plan):
                    raise RuntimeError(f"provider stderr leaked {secret}")

            with MoshRepository(path) as repo:
                repo.register_account(Account("personal", "test", "personal", "ref", AccountStatus.READY))
                repo.create_task(TaskEnvelope("TASK-MALICIOUS", "MOSH", "owner", "test", "safe objective",
                                              provider="test", account_id="personal"))
                queued = repo.enqueue_run("TASK-MALICIOUS")
                self.assertTrue(Worker(repo, "worker", {"personal": MaliciousExecutor()}).run_once())
                run = repo.get_run(queued.run_id)
                self.assertNotIn(secret, run.command_argv_json)
                self.assertEqual(run.command_argv_json.count("<redacted>"), 2)
                self.assertNotIn(secret, run.error)
                self.assertEqual(run.error, "execution failed (RuntimeError)")
                snapshot = repo.task_snapshot("TASK-MALICIOUS")
                public_run = snapshot["runs"][0]
                self.assertFalse(any(key.startswith("command_") for key in public_run))
                self.assertNotIn(secret, str(repo.list_events("TASK-MALICIOUS")))
            self.assertNotIn(secret.encode(), path.read_bytes())


if __name__ == "__main__":
    unittest.main()
