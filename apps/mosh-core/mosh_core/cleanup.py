from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import CleanupOperationStatus, CleanupPlan, CleanupStatus, Risk, SideEffectExecutionStatus
from .repository import MoshRepository


class MarkerCleanup:
    def __init__(self, repository: MoshRepository, root: str | Path, trash_root: str | Path):
        self.repository = repository
        self.root = Path(root).resolve()
        self.trash_root = Path(trash_root).resolve()

    def plan(self, task_id: str, execution_id: str, requested_by: str, expires_at: str,
             retain_until: str) -> tuple[CleanupPlan, object]:
        execution = self.repository.get_side_effect_execution(execution_id)
        if execution is None or execution.status != SideEffectExecutionStatus.COMPLETED:
            raise ValueError("cleanup requires a completed execution")
        source = Path(execution.artifact_path).resolve()
        if source.parent != self.root or not source.exists():
            raise ValueError("cleanup source is outside the marker root or missing")
        actual = hashlib.sha256(source.read_bytes()).hexdigest()
        if actual != execution.artifact_sha256:
            raise ValueError("cleanup source hash mismatch")
        trash = (self.trash_root / f"{execution_id}-{source.name}").resolve()
        if trash.parent != self.trash_root or trash.exists():
            raise ValueError("cleanup trash target is unsafe or already exists")
        payload = {"execution_id": execution_id, "source_path": str(source), "trash_path": str(trash), "artifact_sha256": actual}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        plan = self.repository.create_cleanup_plan(task_id, execution_id, str(source), str(trash), actual, digest, retain_until)
        request = self.repository.request_side_effect(task_id, "trash_marker", plan.plan_id,
            {"plan_digest": digest, **payload}, Risk.LOW, requested_by, expires_at)
        return plan, request

    def trash(self, plan_id: str) -> CleanupPlan:
        plan = self.repository.get_cleanup_plan(plan_id)
        if plan is None or plan.status != CleanupStatus.PLANNED:
            raise ValueError("cleanup plan is not ready")
        request = self._approved_request(plan, "trash_marker")
        source, trash = Path(plan.source_path), Path(plan.trash_path)
        if not source.exists() or hashlib.sha256(source.read_bytes()).hexdigest() != plan.artifact_sha256:
            raise ValueError("cleanup source missing or changed")
        self.trash_root.mkdir(parents=True, exist_ok=True)
        operation = self.repository.begin_cleanup_operation(plan.plan_id, request.request_id, "trash")
        source.replace(trash)
        self.repository.finish_cleanup_operation(operation.operation_id, CleanupStatus.TRASHED)
        return self.repository.get_cleanup_plan(plan_id)

    def request_restore(self, plan_id: str, requested_by: str, expires_at: str):
        plan = self.repository.get_cleanup_plan(plan_id)
        if plan is None or plan.status != CleanupStatus.TRASHED:
            raise ValueError("cleanup plan is not trashed")
        params = {"plan_digest": plan.plan_digest, "source_path": plan.source_path,
                  "trash_path": plan.trash_path, "artifact_sha256": plan.artifact_sha256}
        return self.repository.request_side_effect(plan.task_id, "restore_marker", plan.plan_id,
                                                   params, Risk.LOW, requested_by, expires_at)

    def restore(self, plan_id: str) -> CleanupPlan:
        plan = self.repository.get_cleanup_plan(plan_id)
        if plan is None or plan.status != CleanupStatus.TRASHED:
            raise ValueError("cleanup plan is not restorable")
        request = self._approved_request(plan, "restore_marker")
        source, trash = Path(plan.source_path), Path(plan.trash_path)
        if source.exists() or not trash.exists() or hashlib.sha256(trash.read_bytes()).hexdigest() != plan.artifact_sha256:
            raise ValueError("restore paths are unsafe or trash hash changed")
        operation = self.repository.begin_cleanup_operation(plan.plan_id, request.request_id, "restore")
        trash.replace(source)
        self.repository.finish_cleanup_operation(operation.operation_id, CleanupStatus.RESTORED)
        return self.repository.get_cleanup_plan(plan_id)

    def reconcile_all(self):
        results = []
        for operation in self.repository.list_cleanup_operations(CleanupOperationStatus.PREPARED):
            plan = self.repository.get_cleanup_plan(operation.plan_id)
            source, trash = Path(plan.source_path), Path(plan.trash_path)
            try:
                if operation.action == "trash":
                    if source.exists() and not trash.exists():
                        if hashlib.sha256(source.read_bytes()).hexdigest() != plan.artifact_sha256:
                            raise ValueError("cleanup source hash changed")
                        self.trash_root.mkdir(parents=True, exist_ok=True)
                        source.replace(trash)
                    if source.exists() or not trash.exists() or hashlib.sha256(trash.read_bytes()).hexdigest() != plan.artifact_sha256:
                        raise ValueError("trash operation paths are inconsistent")
                    results.append(self.repository.finish_cleanup_operation(operation.operation_id, CleanupStatus.TRASHED))
                else:
                    if trash.exists() and not source.exists():
                        if hashlib.sha256(trash.read_bytes()).hexdigest() != plan.artifact_sha256:
                            raise ValueError("trash artifact hash changed")
                        source.parent.mkdir(parents=True, exist_ok=True)
                        trash.replace(source)
                    if trash.exists() or not source.exists() or hashlib.sha256(source.read_bytes()).hexdigest() != plan.artifact_sha256:
                        raise ValueError("restore operation paths are inconsistent")
                    results.append(self.repository.finish_cleanup_operation(operation.operation_id, CleanupStatus.RESTORED))
            except (OSError, ValueError) as error:
                results.append(self.repository.fail_cleanup_operation(operation.operation_id, str(error)))
        return results

    def _approved_request(self, plan: CleanupPlan, action: str):
        rows = self.repository.connection.execute(
            "SELECT request_id FROM side_effect_requests WHERE task_id=? AND action=? AND target=? AND status='approved' ORDER BY created_at DESC LIMIT 1",
            (plan.task_id, action, plan.plan_id),
        ).fetchone()
        if rows is None:
            raise ValueError("cleanup action has no approved request")
        return self.repository.get_side_effect(rows["request_id"])
