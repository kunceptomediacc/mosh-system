from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class TaskStatus(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    REVIEW = "review"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class Risk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AccountStatus(StrEnum):
    UNKNOWN = "unknown"
    READY = "ready"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"


class AgentStatus(StrEnum):
    UNKNOWN = "unknown"
    READY = "ready"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    DISABLED = "disabled"


class RunStatus(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"
    CANCELLED = "cancelled"


class ApprovalDecision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class SideEffectStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CONSUMED = "consumed"


class SideEffectExecutionStatus(StrEnum):
    PREPARED = "prepared"
    COMPLETED = "completed"
    FAILED = "failed"


class CleanupStatus(StrEnum):
    PLANNED = "planned"
    TRASHED = "trashed"
    RESTORED = "restored"


class CleanupOperationStatus(StrEnum):
    PREPARED = "prepared"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Account:
    account_id: str
    provider: str
    alias: str
    credential_ref: str
    status: AccountStatus = AccountStatus.UNKNOWN
    metadata: dict[str, Any] = field(default_factory=dict)
    last_verified_at: str | None = None

    def __post_init__(self) -> None:
        if not self.account_id or not self.provider or not self.alias or not self.credential_ref:
            raise ValueError("account identifiers, provider, alias, and credential_ref are required")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Agent:
    agent_id: str
    name: str
    adapter: str
    status: AgentStatus = AgentStatus.UNKNOWN
    capabilities: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.agent_id or not self.name or not self.adapter:
            raise ValueError("agent_id, name, and adapter are required")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["capabilities"] = list(self.capabilities)
        return value


@dataclass(frozen=True, slots=True)
class TaskEnvelope:
    task_id: str
    project_id: str
    from_actor: str
    to: str
    objective: str
    inputs: tuple[Any, ...] = ()
    acceptance: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    risk: Risk = Risk.LOW
    approval_required: bool = False
    status: TaskStatus = TaskStatus.DRAFT
    provider: str | None = None
    account_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    version: int = 1
    cancellation_requested: bool = False
    cancellation_reason: str | None = None

    def __post_init__(self) -> None:
        required = (self.task_id, self.project_id, self.from_actor, self.to, self.objective)
        if not all(required):
            raise ValueError("task_id, project_id, from_actor, to, and objective are required")
        if (self.provider is None) != (self.account_id is None):
            raise ValueError("provider and account_id must be set together")
        if self.version < 1:
            raise ValueError("version must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "from": self.from_actor,
            "to": self.to,
            "provider": self.provider,
            "account_id": self.account_id,
            "objective": self.objective,
            "inputs": list(self.inputs),
            "acceptance": list(self.acceptance),
            "constraints": list(self.constraints),
            "risk": self.risk.value,
            "approval_required": self.approval_required,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
            "cancellation_requested": self.cancellation_requested,
            "cancellation_reason": self.cancellation_reason,
        }


@dataclass(frozen=True, slots=True)
class TaskRun:
    run_id: str
    task_id: str
    attempt: int
    account_id: str
    status: RunStatus = RunStatus.QUEUED
    lease_owner: str | None = None
    lease_expires_at: str | None = None
    result: str | None = None
    error: str | None = None
    completion_key: str | None = None
    adapter_name: str | None = None
    command_argv_json: str | None = None
    command_env_keys_json: str | None = None
    command_sha256: str | None = None
    command_recorded_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Approval:
    approval_id: str
    task_id: str
    scope: str
    requested_by: str
    decision: ApprovalDecision = ApprovalDecision.PENDING
    decided_by: str | None = None
    reason: str | None = None
    created_at: str | None = None
    decided_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["decision"] = self.decision.value
        return value


@dataclass(frozen=True, slots=True)
class SideEffectRequest:
    request_id: str
    task_id: str
    action: str
    target: str
    parameters: dict[str, Any]
    risk: Risk
    request_digest: str
    status: SideEffectStatus
    requested_by: str
    decided_by: str | None = None
    reason: str | None = None
    expires_at: str | None = None
    created_at: str | None = None
    decided_at: str | None = None
    consumed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["risk"] = self.risk.value
        value["status"] = self.status.value
        return value


@dataclass(frozen=True, slots=True)
class SideEffectExecution:
    execution_id: str
    request_id: str
    idempotency_key: str
    status: SideEffectExecutionStatus
    artifact_path: str
    artifact_sha256: str
    error: str | None = None
    prepared_at: str | None = None
    completed_at: str | None = None
    failed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value


@dataclass(frozen=True, slots=True)
class CleanupPlan:
    plan_id: str
    task_id: str
    execution_id: str
    source_path: str
    trash_path: str
    artifact_sha256: str
    plan_digest: str
    status: CleanupStatus
    created_at: str | None = None
    trashed_at: str | None = None
    restored_at: str | None = None
    retain_until: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value


@dataclass(frozen=True, slots=True)
class CleanupOperation:
    operation_id: str
    plan_id: str
    request_id: str
    action: str
    status: CleanupOperationStatus
    error: str | None = None
    prepared_at: str | None = None
    completed_at: str | None = None
    failed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value
