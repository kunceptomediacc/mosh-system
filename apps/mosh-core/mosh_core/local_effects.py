from __future__ import annotations

import hashlib
from pathlib import Path

from .models import SideEffectExecution, SideEffectExecutionStatus
from .repository import MoshRepository


class LocalMarkerExecutor:
    def __init__(self, repository: MoshRepository, root: str | Path):
        self.repository = repository
        self.root = Path(root).resolve()

    def execute(self, request_id: str) -> SideEffectExecution:
        request = self.repository.get_side_effect(request_id)
        if request is None:
            raise KeyError(request_id)
        if request.action != "write_marker":
            raise ValueError("unsupported local side effect")
        if Path(request.target).name != request.target or not request.target.endswith(".txt"):
            raise ValueError("marker target must be a safe .txt filename")
        content = request.parameters.get("content")
        if not isinstance(content, str) or len(content.encode("utf-8")) > 4096:
            raise ValueError("marker content must be a UTF-8 string of at most 4096 bytes")
        artifact = (self.root / request.target).resolve()
        if artifact.parent != self.root:
            raise ValueError("marker target escapes the side-effect root")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        execution = self.repository.begin_side_effect_execution(
            request_id, request.action, request.target, request.parameters, str(artifact), digest
        )
        return self._materialize(execution, content)

    def recover(self, execution_id: str) -> SideEffectExecution:
        execution = self.repository.get_side_effect_execution(execution_id)
        if execution is None:
            raise KeyError(execution_id)
        request = self.repository.get_side_effect(execution.request_id)
        content = request.parameters.get("content") if request else None
        if not isinstance(content, str):
            raise ValueError("execution has no recoverable marker content")
        try:
            return self._materialize(execution, content)
        except (OSError, ValueError) as error:
            return self.repository.fail_side_effect_execution(execution_id, str(error))

    def reconcile_all(self) -> list[SideEffectExecution]:
        prepared = self.repository.list_side_effect_executions(SideEffectExecutionStatus.PREPARED)
        return [self.recover(execution.execution_id) for execution in prepared]

    def _materialize(self, execution: SideEffectExecution, content: str) -> SideEffectExecution:
        if execution.status == SideEffectExecutionStatus.COMPLETED:
            return execution
        artifact = Path(execution.artifact_path)
        self.root.mkdir(parents=True, exist_ok=True)
        if artifact.exists():
            actual = hashlib.sha256(artifact.read_bytes()).hexdigest()
            if actual != execution.artifact_sha256:
                raise ValueError("existing marker does not match prepared execution")
        else:
            with artifact.open("x", encoding="utf-8", newline="") as stream:
                stream.write(content)
        return self.repository.complete_side_effect_execution(execution.execution_id)
