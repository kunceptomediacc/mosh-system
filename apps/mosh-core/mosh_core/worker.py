from __future__ import annotations

import threading
from typing import Protocol

from .adapters.base import ExecutionPlan
from .repository import MoshRepository


class Executor(Protocol):
    def build_plan(self, objective: str) -> ExecutionPlan: ...
    def execute_plan(self, plan: ExecutionPlan) -> str: ...


def persistent_error(error: Exception) -> str:
    """Return bounded diagnostic metadata without persisting exception text or stderr."""
    name = type(error).__name__
    safe_name = "".join(character for character in name if character.isalnum() or character == "_")[:80]
    return f"execution failed ({safe_name or 'Exception'})"


class Worker:
    def __init__(self, repository: MoshRepository, worker_id: str, executors: dict[str, Executor],
                 lease_seconds: int = 60, renewal_interval: float = 20):
        self.repository = repository
        self.worker_id = worker_id
        self.executors = executors
        self.lease_seconds = lease_seconds
        self.renewal_interval = renewal_interval

    def run_once(self) -> bool:
        claimed = self.repository.claim_next(self.worker_id, lease_seconds=self.lease_seconds)
        if claimed is None:
            return False
        task = self.repository.get_task(claimed.task_id)
        if task is None:
            raise RuntimeError("claimed run has no task")
        executor = self.executors.get(claimed.account_id)
        if executor is None:
            self.repository.mark_run_running(claimed.run_id, self.worker_id)
            self.repository.fail_run(claimed.run_id, self.worker_id, "no executor for selected account")
            return True
        self.repository.mark_run_running(claimed.run_id, self.worker_id)
        current = self.repository.get_task(claimed.task_id)
        if current and current.cancellation_requested:
            self.repository.cancel_run(claimed.run_id, self.worker_id)
            return True
        try:
            stop = threading.Event()
            heartbeat_error: list[Exception] = []

            def heartbeat() -> None:
                with MoshRepository(self.repository.path) as heartbeat_repository:
                    while not stop.wait(self.renewal_interval):
                        try:
                            heartbeat_repository.renew_lease(
                                claimed.run_id, self.worker_id, lease_seconds=self.lease_seconds
                            )
                        except Exception as error:
                            heartbeat_error.append(error)
                            return

            thread = threading.Thread(target=heartbeat, name=f"lease-{claimed.run_id}", daemon=True)
            thread.start()
            try:
                plan = executor.build_plan(task.objective)
                argv_json, env_keys_json, evidence_sha256 = plan.evidence()
                self.repository.record_execution_plan(
                    claimed.run_id, self.worker_id, plan.adapter_name,
                    argv_json, env_keys_json, evidence_sha256,
                )
                result = executor.execute_plan(plan)
            finally:
                stop.set()
                thread.join(timeout=max(1.0, self.renewal_interval * 2))
            if heartbeat_error:
                raise RuntimeError(f"lease renewal failed: {heartbeat_error[0]}")
            current = self.repository.get_task(claimed.task_id)
            if current and current.cancellation_requested:
                self.repository.cancel_run(claimed.run_id, self.worker_id)
            else:
                self.repository.complete_run(claimed.run_id, self.worker_id, result, completion_key=claimed.run_id)
        except Exception as error:
            self.repository.fail_run(claimed.run_id, self.worker_id, persistent_error(error))
        return True
