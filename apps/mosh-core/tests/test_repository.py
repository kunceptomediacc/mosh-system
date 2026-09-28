import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "mosh-core"))

from mosh_core.models import Account, AccountStatus, Risk, RunStatus, SideEffectStatus, TaskEnvelope, TaskStatus
from mosh_core.repository import MoshRepository


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "mosh.db"

    def tearDown(self):
        self.temp.cleanup()

    def test_task_survives_repository_restart_with_events(self):
        account = Account("codex-personal", "codex", "personal", "codex-home:personal", AccountStatus.READY)
        task = TaskEnvelope("TASK-1", "MOSH", "owner", "codex", "Prove durability", provider="codex", account_id=account.account_id)
        with MoshRepository(self.db) as repo:
            repo.register_account(account)
            repo.create_task(task)
            repo.transition(task.task_id, TaskStatus.QUEUED)
            repo.transition(task.task_id, TaskStatus.CLAIMED)
        with MoshRepository(self.db) as reopened:
            recovered = reopened.get_task(task.task_id)
            self.assertIsNotNone(recovered)
            self.assertEqual(recovered.status, TaskStatus.CLAIMED)
            self.assertEqual(recovered.account_id, "codex-personal")
            self.assertEqual(len(reopened.list_events(task.task_id)), 3)

    def test_invalid_transition_is_rejected(self):
        with MoshRepository(self.db) as repo:
            repo.create_task(TaskEnvelope("TASK-2", "MOSH", "owner", "codex", "Invalid transition"))
            with self.assertRaises(ValueError):
                repo.transition("TASK-2", TaskStatus.COMPLETED)

    def test_unknown_or_mismatched_account_is_rejected(self):
        with MoshRepository(self.db) as repo:
            with self.assertRaises(ValueError):
                repo.create_task(TaskEnvelope("TASK-3", "MOSH", "owner", "codex", "Unknown", provider="codex", account_id="missing"))
            repo.register_account(Account("work", "codex", "work", "codex-home:work", AccountStatus.READY))
            with self.assertRaises(ValueError):
                repo.create_task(TaskEnvelope("TASK-4", "MOSH", "owner", "codex", "Mismatch", provider="hermes", account_id="work"))

    def test_expired_lease_creates_replacement_attempt(self):
        with MoshRepository(self.db) as repo:
            repo.register_account(Account("personal", "codex", "personal", "ref", AccountStatus.READY))
            repo.create_task(TaskEnvelope("TASK-5", "MOSH", "owner", "codex", "Recover", provider="codex", account_id="personal"))
            first = repo.enqueue_run("TASK-5")
            claimed = repo.claim_next("worker-1", lease_seconds=-1)
            self.assertEqual(claimed.run_id, first.run_id)
            replacements = repo.recover_expired_runs()
            self.assertEqual(len(replacements), 1)
            self.assertEqual(replacements[0].attempt, 2)
            self.assertEqual(repo.get_run(first.run_id).status, RunStatus.ABANDONED)
            self.assertEqual(repo.get_task("TASK-5").status, TaskStatus.QUEUED)

    def test_manual_reroute_updates_queued_run_and_audits(self):
        with MoshRepository(self.db) as repo:
            for alias in ("personal", "work"):
                repo.register_account(Account(alias, "codex", alias, f"ref:{alias}", AccountStatus.READY))
            repo.create_task(TaskEnvelope("TASK-6", "MOSH", "owner", "codex", "Reroute", provider="codex", account_id="personal"))
            run = repo.enqueue_run("TASK-6")
            updated = repo.reroute_task("TASK-6", "work", "owner requested")
            self.assertEqual(updated.account_id, "work")
            self.assertEqual(repo.get_run(run.run_id).account_id, "work")
            self.assertEqual(repo.list_events("TASK-6")[-1]["event_type"], "task.rerouted")

    def test_lease_renewal_and_expiry_rules(self):
        with MoshRepository(self.db) as repo:
            repo.register_account(Account("personal", "codex", "personal", "ref", AccountStatus.READY))
            repo.create_task(TaskEnvelope("TASK-7", "MOSH", "owner", "codex", "Lease", provider="codex", account_id="personal"))
            repo.enqueue_run("TASK-7")
            claimed = repo.claim_next("worker", lease_seconds=30)
            renewed = repo.renew_lease(claimed.run_id, "worker", lease_seconds=120)
            self.assertGreater(renewed.lease_expires_at, claimed.lease_expires_at)

    def test_queued_cancel_is_immediate(self):
        with MoshRepository(self.db) as repo:
            repo.register_account(Account("personal", "codex", "personal", "ref", AccountStatus.READY))
            repo.create_task(TaskEnvelope("TASK-8", "MOSH", "owner", "codex", "Cancel", provider="codex", account_id="personal"))
            run = repo.enqueue_run("TASK-8")
            task = repo.request_cancel("TASK-8", "owner stopped it")
            self.assertEqual(task.status, TaskStatus.CANCELLED)
            self.assertEqual(repo.get_run(run.run_id).status, RunStatus.CANCELLED)

    def test_completion_is_idempotent_for_same_key(self):
        with MoshRepository(self.db) as repo:
            repo.register_account(Account("personal", "codex", "personal", "ref", AccountStatus.READY))
            repo.create_task(TaskEnvelope("TASK-9", "MOSH", "owner", "codex", "Once", provider="codex", account_id="personal"))
            repo.enqueue_run("TASK-9")
            claimed = repo.claim_next("worker")
            repo.mark_run_running(claimed.run_id, "worker")
            first = repo.complete_run(claimed.run_id, "worker", "result", completion_key="key-1")
            second = repo.complete_run(claimed.run_id, "worker", "ignored", completion_key="key-1")
            self.assertEqual(first.result, second.result)
            completed = [event for event in repo.list_events("TASK-9") if event["event_type"] == "run.completed"]
            self.assertEqual(len(completed), 1)

    def test_approval_required_run_cannot_be_claimed_until_approved(self):
        with MoshRepository(self.db) as repo:
            repo.register_account(Account("personal", "codex", "personal", "ref", AccountStatus.READY))
            repo.create_task(TaskEnvelope("TASK-APPROVE", "MOSH", "owner", "codex", "Execute", provider="codex", account_id="personal", approval_required=True))
            run = repo.enqueue_run("TASK-APPROVE")
            self.assertIsNone(repo.claim_next("worker"))
            approval = repo.list_approvals("TASK-APPROVE")[0]
            self.assertEqual(approval.decision.value, "pending")
            repo.decide_approval("TASK-APPROVE", True, "owner", "approved for execution")
            self.assertEqual(repo.claim_next("worker").run_id, run.run_id)

    def test_rejected_approval_terminates_queued_work(self):
        with MoshRepository(self.db) as repo:
            repo.register_account(Account("personal", "codex", "personal", "ref", AccountStatus.READY))
            repo.create_task(TaskEnvelope("TASK-REJECT", "MOSH", "owner", "codex", "Execute", provider="codex", account_id="personal", approval_required=True))
            run = repo.enqueue_run("TASK-REJECT")
            repo.decide_approval("TASK-REJECT", False, "owner", "not authorized")
            self.assertEqual(repo.get_task("TASK-REJECT").status, TaskStatus.REJECTED)
            self.assertEqual(repo.get_run(run.run_id).status, RunStatus.CANCELLED)
            with self.assertRaises(ValueError):
                repo.decide_approval("TASK-REJECT", True, "owner", "changed mind")

    def test_side_effect_approval_is_digest_bound_and_single_use(self):
        expires = (datetime.now(UTC) + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
        with MoshRepository(self.db) as repo:
            repo.create_task(TaskEnvelope("TASK-SFX", "MOSH", "owner", "agent", "Prepare email"))
            request = repo.request_side_effect(
                "TASK-SFX", "send_email", "a@example.com", {"subject": "Hello"}, Risk.HIGH, "agent", expires
            )
            with self.assertRaises(ValueError):
                repo.decide_side_effect(request.request_id, "0" * 64, True, "owner", "wrong digest")
            approved = repo.decide_side_effect(
                request.request_id, request.request_digest, True, "owner", "approved exact email"
            )
            self.assertEqual(approved.status, SideEffectStatus.APPROVED)
            with self.assertRaises(ValueError):
                repo.consume_side_effect(request.request_id, "send_email", "b@example.com", {"subject": "Hello"})
            consumed = repo.consume_side_effect(
                request.request_id, "send_email", "a@example.com", {"subject": "Hello"}
            )
            self.assertEqual(consumed.status, SideEffectStatus.CONSUMED)
            with self.assertRaises(ValueError):
                repo.consume_side_effect(request.request_id, "send_email", "a@example.com", {"subject": "Hello"})

    def test_side_effect_expiry_and_rejection_are_enforced(self):
        expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        future = (datetime.now(UTC) + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
        with MoshRepository(self.db) as repo:
            repo.create_task(TaskEnvelope("TASK-SFX2", "MOSH", "owner", "agent", "External action"))
            old = repo.request_side_effect("TASK-SFX2", "deploy", "prod", {}, Risk.CRITICAL, "agent", expired)
            with self.assertRaises(ValueError):
                repo.decide_side_effect(old.request_id, old.request_digest, True, "owner", "too late")
            denied = repo.request_side_effect("TASK-SFX2", "deploy", "staging", {}, Risk.HIGH, "agent", future)
            rejected = repo.decide_side_effect(denied.request_id, denied.request_digest, False, "owner", "denied")
            self.assertEqual(rejected.status, SideEffectStatus.REJECTED)
            with self.assertRaises(ValueError):
                repo.consume_side_effect(denied.request_id, "deploy", "staging", {})


if __name__ == "__main__":
    unittest.main()
