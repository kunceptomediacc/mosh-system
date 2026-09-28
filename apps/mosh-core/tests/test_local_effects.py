import hashlib
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "mosh-core"))

from mosh_core.local_effects import LocalMarkerExecutor
from mosh_core.models import Risk, SideEffectExecutionStatus, TaskEnvelope
from mosh_core.repository import MoshRepository


class LocalEffectTests(unittest.TestCase):
    def test_marker_recovers_after_write_before_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "mosh.db"
            artifacts = root / "effects"
            expires = (datetime.now(UTC) + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
            with MoshRepository(database) as repo:
                repo.create_task(TaskEnvelope("TASK-MARKER", "MOSH", "owner", "local", "write marker"))
                request = repo.request_side_effect("TASK-MARKER", "write_marker", "accepted.txt", {"content": "ok"}, Risk.LOW, "agent", expires)
                repo.decide_side_effect(request.request_id, request.request_digest, True, "owner", "accept")
                artifact = (artifacts / "accepted.txt").resolve()
                digest = hashlib.sha256(b"ok").hexdigest()
                prepared = repo.begin_side_effect_execution(request.request_id, "write_marker", "accepted.txt", {"content": "ok"}, str(artifact), digest)
                artifacts.mkdir()
                artifact.write_text("ok", encoding="utf-8")
            with MoshRepository(database) as reopened:
                completed = LocalMarkerExecutor(reopened, artifacts).recover(prepared.execution_id)
                self.assertEqual(completed.status, SideEffectExecutionStatus.COMPLETED)
                again = LocalMarkerExecutor(reopened, artifacts).execute(request.request_id)
                self.assertEqual(again.execution_id, completed.execution_id)
                self.assertEqual(artifact.read_text(encoding="utf-8"), "ok")

    def test_marker_rejects_path_traversal_before_consumption(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expires = (datetime.now(UTC) + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
            with MoshRepository(root / "mosh.db") as repo:
                repo.create_task(TaskEnvelope("TASK-BAD-MARKER", "MOSH", "owner", "local", "bad marker"))
                request = repo.request_side_effect("TASK-BAD-MARKER", "write_marker", "../escape.txt", {"content": "no"}, Risk.LOW, "agent", expires)
                repo.decide_side_effect(request.request_id, request.request_digest, True, "owner", "test")
                with self.assertRaises(ValueError):
                    LocalMarkerExecutor(repo, root / "effects").execute(request.request_id)
                self.assertEqual(repo.get_side_effect(request.request_id).status.value, "approved")

    def test_reconcile_all_recreates_missing_and_fails_mismatched_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "effects"
            expires = (datetime.now(UTC) + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
            with MoshRepository(root / "mosh.db") as repo:
                repo.create_task(TaskEnvelope("TASK-RECON", "MOSH", "owner", "local", "reconcile"))
                executions = []
                for target, content in (("missing.txt", "restore"), ("mismatch.txt", "expected")):
                    request = repo.request_side_effect("TASK-RECON", "write_marker", target, {"content": content}, Risk.LOW, "agent", expires)
                    repo.decide_side_effect(request.request_id, request.request_digest, True, "owner", "accept")
                    artifact = (artifacts / target).resolve()
                    executions.append(repo.begin_side_effect_execution(
                        request.request_id, "write_marker", target, {"content": content}, str(artifact),
                        hashlib.sha256(content.encode()).hexdigest()
                    ))
                artifacts.mkdir()
                (artifacts / "mismatch.txt").write_text("tampered", encoding="utf-8")
                results = LocalMarkerExecutor(repo, artifacts).reconcile_all()
                self.assertEqual(results[0].status, SideEffectExecutionStatus.COMPLETED)
                self.assertEqual(results[1].status, SideEffectExecutionStatus.FAILED)
                self.assertEqual((artifacts / "missing.txt").read_text(encoding="utf-8"), "restore")
                self.assertEqual((artifacts / "mismatch.txt").read_text(encoding="utf-8"), "tampered")


if __name__ == "__main__":
    unittest.main()
