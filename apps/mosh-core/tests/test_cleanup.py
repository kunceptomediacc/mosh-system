import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "mosh-core"))

from mosh_core.cleanup import MarkerCleanup
from mosh_core.local_effects import LocalMarkerExecutor
from mosh_core.models import CleanupStatus, Risk, TaskEnvelope
from mosh_core.repository import MoshRepository


class CleanupTests(unittest.TestCase):
    def test_cleanup_requires_separate_approved_trash_and_restore_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            markers, trash = base / "markers", base / "trash"
            expires = (datetime.now(UTC) + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
            with MoshRepository(base / "mosh.db") as repo:
                repo.create_task(TaskEnvelope("TASK-CLEAN", "MOSH", "owner", "local", "cleanup"))
                request = repo.request_side_effect("TASK-CLEAN", "write_marker", "clean.txt", {"content": "keep"}, Risk.LOW, "agent", expires)
                repo.decide_side_effect(request.request_id, request.request_digest, True, "owner", "write")
                execution = LocalMarkerExecutor(repo, markers).execute(request.request_id)
                cleanup = MarkerCleanup(repo, markers, trash)
                plan, trash_request = cleanup.plan("TASK-CLEAN", execution.execution_id, "agent", expires, expires)
                self.assertTrue((markers / "clean.txt").exists())
                with self.assertRaises(ValueError):
                    cleanup.trash(plan.plan_id)
                repo.decide_side_effect(trash_request.request_id, trash_request.request_digest, True, "owner", "trash")
                self.assertEqual(cleanup.trash(plan.plan_id).status, CleanupStatus.TRASHED)
                self.assertFalse((markers / "clean.txt").exists())
                self.assertTrue(Path(plan.trash_path).exists())
                restore_request = cleanup.request_restore(plan.plan_id, "agent", expires)
                repo.decide_side_effect(restore_request.request_id, restore_request.request_digest, True, "owner", "restore")
                self.assertEqual(cleanup.restore(plan.plan_id).status, CleanupStatus.RESTORED)
                self.assertEqual((markers / "clean.txt").read_text(encoding="utf-8"), "keep")

    def test_reconcile_finishes_move_after_prepared_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            markers, trash = base / "markers", base / "trash"
            expires = (datetime.now(UTC) + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
            with MoshRepository(base / "mosh.db") as repo:
                repo.create_task(TaskEnvelope("TASK-CRASH-CLEAN", "MOSH", "owner", "local", "cleanup"))
                write = repo.request_side_effect("TASK-CRASH-CLEAN", "write_marker", "crash.txt", {"content": "safe"}, Risk.LOW, "agent", expires)
                repo.decide_side_effect(write.request_id, write.request_digest, True, "owner", "write")
                execution = LocalMarkerExecutor(repo, markers).execute(write.request_id)
                cleanup = MarkerCleanup(repo, markers, trash)
                plan, request = cleanup.plan("TASK-CRASH-CLEAN", execution.execution_id, "agent", expires, expires)
                repo.decide_side_effect(request.request_id, request.request_digest, True, "owner", "trash")
                operation = repo.begin_cleanup_operation(plan.plan_id, request.request_id, "trash")
                trash.mkdir()
                Path(plan.source_path).replace(plan.trash_path)
            with MoshRepository(base / "mosh.db") as reopened:
                results = MarkerCleanup(reopened, markers, trash).reconcile_all()
                self.assertEqual(results[0].operation_id, operation.operation_id)
                self.assertEqual(results[0].status.value, "completed")
                self.assertEqual(reopened.get_cleanup_plan(plan.plan_id).status, CleanupStatus.TRASHED)


if __name__ == "__main__":
    unittest.main()
