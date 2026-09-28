from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .db import connect, migrate
from .models import Account, AccountStatus, Agent, AgentStatus, Approval, ApprovalDecision, CleanupOperation, CleanupOperationStatus, CleanupPlan, CleanupStatus, Risk, RunStatus, SideEffectExecution, SideEffectExecutionStatus, SideEffectRequest, SideEffectStatus, TaskEnvelope, TaskRun, TaskStatus
from .memory_governance import sha256_text, validate_memory_candidate, validate_procedure_ref


ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.DRAFT: {TaskStatus.QUEUED, TaskStatus.CANCELLED, TaskStatus.REJECTED},
    TaskStatus.QUEUED: {TaskStatus.CLAIMED, TaskStatus.BLOCKED, TaskStatus.CANCELLED, TaskStatus.FAILED},
    TaskStatus.CLAIMED: {TaskStatus.RUNNING, TaskStatus.BLOCKED, TaskStatus.CANCELLED, TaskStatus.FAILED},
    TaskStatus.RUNNING: {TaskStatus.REVIEW, TaskStatus.BLOCKED, TaskStatus.CANCELLED, TaskStatus.FAILED},
    TaskStatus.REVIEW: {TaskStatus.COMPLETED, TaskStatus.RUNNING, TaskStatus.REJECTED, TaskStatus.BLOCKED},
    TaskStatus.BLOCKED: {TaskStatus.QUEUED, TaskStatus.CANCELLED, TaskStatus.FAILED},
    TaskStatus.FAILED: {TaskStatus.QUEUED, TaskStatus.CANCELLED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.CANCELLED: set(),
    TaskStatus.REJECTED: set(),
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def side_effect_digest(action: str, target: str, parameters: dict) -> str:
    canonical = json.dumps(
        {"action": action, "target": target, "parameters": parameters},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class MoshRepository:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.connection = connect(self.path)
        migrate(self.connection)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "MoshRepository":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def register_account(self, account: Account) -> None:
        now = utc_now()
        self.connection.execute(
            """INSERT INTO accounts(account_id,provider,alias,credential_ref,status,metadata_json,last_verified_at,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(account_id) DO UPDATE SET provider=excluded.provider,alias=excluded.alias,
               credential_ref=excluded.credential_ref,status=excluded.status,metadata_json=excluded.metadata_json,
               last_verified_at=excluded.last_verified_at,updated_at=excluded.updated_at""",
            (account.account_id, account.provider, account.alias, account.credential_ref, account.status.value,
             json.dumps(account.metadata, separators=(",", ":")), account.last_verified_at, now, now),
        )

    def register_agent(self, agent: Agent) -> None:
        now = utc_now()
        self.connection.execute(
            """INSERT INTO agents(agent_id,name,adapter,status,capabilities_json,metadata_json,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(agent_id) DO UPDATE SET name=excluded.name,adapter=excluded.adapter,status=excluded.status,
               capabilities_json=excluded.capabilities_json,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
            (agent.agent_id, agent.name, agent.adapter, agent.status.value, json.dumps(agent.capabilities),
             json.dumps(agent.metadata, separators=(",", ":")), now, now),
        )

    def register_plugin_definition(self, plugin_id: str, name: str, provider: str, auth_type: str,
                                   capabilities: list[str], requested_scopes: list[str]) -> dict:
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,63}", plugin_id):
            raise ValueError("plugin ID must be a bounded lowercase identifier")
        if not name.strip() or not provider.strip() or auth_type not in {"oauth", "github_app", "token", "none"}:
            raise ValueError("plugin definition requires name, provider, and supported auth type")
        for label, values in (("capability", capabilities), ("scope", requested_scopes)):
            if len(values) > 50 or len(set(values)) != len(values) or any(not re.fullmatch(r"[A-Za-z0-9:._/-]{1,100}", item) for item in values):
                raise ValueError(f"invalid {label} list")
        now = utc_now()
        self.connection.execute(
            """INSERT INTO plugin_registrations(
                   plugin_id,name,provider,status,auth_type,health_status,capabilities_json,
                   requested_scopes_json,granted_scopes_json,last_checked_at,created_at,updated_at
               ) VALUES(?,?,?,'unconfigured',?,'unknown',?,?,'[]',NULL,?,?)
               ON CONFLICT(plugin_id) DO UPDATE SET name=excluded.name,provider=excluded.provider,
                   auth_type=excluded.auth_type,capabilities_json=excluded.capabilities_json,
                   requested_scopes_json=excluded.requested_scopes_json,updated_at=excluded.updated_at""",
            (plugin_id, name.strip(), provider.strip(), auth_type,
             json.dumps(capabilities, separators=(",", ":")),
             json.dumps(requested_scopes, separators=(",", ":")), now, now),
        )
        return self.get_plugin_public(plugin_id)

    def get_plugin_public(self, plugin_id: str) -> dict:
        row = self.connection.execute(
            """SELECT plugin_id,name,provider,status,auth_type,health_status,capabilities_json,
                      requested_scopes_json,granted_scopes_json,last_checked_at
               FROM plugin_registrations WHERE plugin_id=?""", (plugin_id,),
        ).fetchone()
        if row is None:
            raise KeyError(plugin_id)
        result = dict(row)
        for field in ("capabilities_json", "requested_scopes_json", "granted_scopes_json"):
            result[field.removesuffix("_json")] = json.loads(result.pop(field))
        result["allowed_agents"] = [item[0] for item in self.connection.execute(
            "SELECT agent_id FROM plugin_agent_access WHERE plugin_id=? ORDER BY agent_id", (plugin_id,)
        )]
        result["approval_required_for_writes"] = True
        return result

    def list_plugins_public(self) -> list[dict]:
        identifiers = [row[0] for row in self.connection.execute(
            "SELECT plugin_id FROM plugin_registrations ORDER BY name,plugin_id"
        )]
        return [self.get_plugin_public(plugin_id) for plugin_id in identifiers]

    def plugin_activity_public(self) -> dict:
        allowlisted = int(self.connection.execute(
            """SELECT count(DISTINCT repository_full_name)
               FROM plugin_repository_access_grants
               WHERE plugin_id='github' AND revoked_at IS NULL
                 AND (expires_at IS NULL OR expires_at>?)""",
            (utc_now(),),
        ).fetchone()[0])
        total = int(self.connection.execute("SELECT count(*) FROM plugin_read_events").fetchone()[0])
        completed = int(self.connection.execute(
            "SELECT count(*) FROM plugin_read_outcomes WHERE status='completed'"
        ).fetchone()[0])
        failed = int(self.connection.execute(
            "SELECT count(*) FROM plugin_read_outcomes WHERE status='failed'"
        ).fetchone()[0])
        last_activity = self.connection.execute("SELECT max(created_at) FROM plugin_read_events").fetchone()[0]
        return {
            "allowlisted_repositories": allowlisted,
            "reads": {"authorized": total, "completed": completed, "failed": failed,
                      "pending": total - completed - failed},
            "last_activity_at": last_activity,
        }

    def configure_project_governance(self, project_id: str, profile: str, classification: str,
                                     isolation_key: str, actor: str, activate: bool = False) -> dict:
        if actor != "owner":
            raise ValueError("project governance requires owner authorization")
        if profile not in {"experiment", "business"} or classification not in {"internal", "confidential", "restricted"}:
            raise ValueError("invalid governance profile or classification")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,99}", project_id):
            raise ValueError("project ID must be bounded")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{7,63}", isolation_key):
            raise ValueError("isolation key must be a bounded opaque identifier")
        if profile == "business" and activate:
            raise ValueError("business governance must begin in draft and satisfy required evidence before activation")
        status, now = ("active" if activate else "draft"), utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self.connection.execute(
                "SELECT 1 FROM project_governance WHERE project_id=?", (project_id,)
            ).fetchone()
            if existing:
                raise ValueError("project governance is immutable; create a reviewed change event in a later slice")
            self.connection.execute(
                """INSERT INTO project_governance(project_id,profile,classification,isolation_key,status,approved_by,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (project_id, profile, classification, isolation_key, status, actor, now, now),
            )
            self.connection.execute(
                """INSERT INTO project_governance_events(project_id,action,actor,profile,classification,isolation_sha256,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (project_id, "activated" if activate else "configured", actor, profile, classification,
                 hashlib.sha256(isolation_key.encode()).hexdigest(), now),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return dict(self.connection.execute(
            """SELECT project_id,profile,classification,status,compliance_claims_prohibited,approved_by,created_at,updated_at
               FROM project_governance WHERE project_id=?""", (project_id,)
        ).fetchone())

    def add_governance_requirement(self, project_id: str, requirement_key: str, required: bool,
                                   actor: str) -> dict:
        if actor != "owner":
            raise ValueError("governance requirements require owner authorization")
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{2,79}", requirement_key):
            raise ValueError("requirement key must be bounded")
        if self.connection.execute("SELECT 1 FROM project_governance WHERE project_id=?", (project_id,)).fetchone() is None:
            raise ValueError("project governance does not exist")
        requirement_id, now = f"GREQ-{uuid.uuid4().hex.upper()}", utc_now()
        self.connection.execute(
            """INSERT INTO governance_requirements(requirement_id,project_id,requirement_key,required,status,created_at,updated_at)
               VALUES(?,?,?,?, 'gap',?,?)""",
            (requirement_id, project_id, requirement_key, int(required), now, now),
        )
        return dict(self.connection.execute("SELECT * FROM governance_requirements WHERE requirement_id=?", (requirement_id,)).fetchone())

    def decide_governance_requirement(self, requirement_id: str, decision: str, actor: str,
                                      evidence_sha256: str | None = None) -> dict:
        if actor != "owner":
            raise ValueError("governance evidence decisions require owner authorization")
        row = self.connection.execute("SELECT * FROM governance_requirements WHERE requirement_id=?", (requirement_id,)).fetchone()
        if row is None:
            raise KeyError(requirement_id)
        if row["status"] != "gap":
            raise ValueError("governance requirement decision is immutable")
        if decision == "evidenced":
            if not evidence_sha256 or not re.fullmatch(r"[0-9a-f]{64}", evidence_sha256):
                raise ValueError("evidenced requirement requires lowercase SHA-256")
        elif decision == "waived":
            if row["required"] or evidence_sha256 is not None:
                raise ValueError("only optional requirements may be waived without evidence")
        else:
            raise ValueError("decision must be evidenced or waived")
        self.connection.execute(
            "UPDATE governance_requirements SET status=?,evidence_sha256=?,decided_by=?,updated_at=? WHERE requirement_id=?",
            (decision, evidence_sha256, actor, utc_now(), requirement_id),
        )
        return dict(self.connection.execute("SELECT * FROM governance_requirements WHERE requirement_id=?", (requirement_id,)).fetchone())

    def activate_project_governance(self, project_id: str, actor: str) -> dict:
        if actor != "owner":
            raise ValueError("project activation requires owner authorization")
        project = self.connection.execute("SELECT * FROM project_governance WHERE project_id=?", (project_id,)).fetchone()
        if project is None or project["status"] != "draft":
            raise ValueError("only draft project governance can be activated")
        gaps = self.connection.execute(
            "SELECT count(*) FROM governance_requirements WHERE project_id=? AND required=1 AND status!='evidenced'", (project_id,)
        ).fetchone()[0]
        required_total = self.connection.execute(
            "SELECT count(*) FROM governance_requirements WHERE project_id=? AND required=1", (project_id,)
        ).fetchone()[0]
        if project["profile"] == "business" and (required_total == 0 or gaps):
            raise ValueError("business activation requires all required evidence and at least one required control")
        now = utc_now()
        self.connection.execute("UPDATE project_governance SET status='active',updated_at=? WHERE project_id=?", (now, project_id))
        self.connection.execute(
            """INSERT INTO project_governance_events(project_id,action,actor,profile,classification,isolation_sha256,created_at)
               VALUES(?,'activated',?,?,?,?,?)""",
            (project_id, actor, project["profile"], project["classification"],
             hashlib.sha256(project["isolation_key"].encode()).hexdigest(), now),
        )
        return dict(self.connection.execute(
            "SELECT project_id,profile,classification,status,compliance_claims_prohibited,approved_by,created_at,updated_at FROM project_governance WHERE project_id=?",
            (project_id,),
        ).fetchone())

    def create_change_request(self, project_id: str, change_type: str, risk: str,
                              description_sha256: str, evidence_sha256: str, requested_by: str) -> dict:
        if change_type not in {"configuration", "code", "deployment", "plugin", "data"}:
            raise ValueError("invalid change type")
        if risk not in {"low", "medium", "high", "critical"}:
            raise ValueError("invalid change risk")
        if not requested_by.strip() or any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in (description_sha256, evidence_sha256)):
            raise ValueError("change request requires actor and lowercase SHA-256 evidence")
        if self.connection.execute("SELECT 1 FROM project_governance WHERE project_id=?", (project_id,)).fetchone() is None:
            raise ValueError("project governance does not exist")
        canonical = json.dumps({"project_id": project_id, "change_type": change_type, "risk": risk,
                                "description_sha256": description_sha256, "evidence_sha256": evidence_sha256},
                               sort_keys=True, separators=(",", ":"))
        request_sha256 = hashlib.sha256(canonical.encode()).hexdigest()
        change_id, now = f"CHG-{uuid.uuid4().hex.upper()}", utc_now()
        self.connection.execute(
            """INSERT INTO change_requests(change_id,project_id,change_type,risk,description_sha256,evidence_sha256,
                   request_sha256,status,requested_by,created_at) VALUES(?,?,?,?,?,?,?,'pending',?,?)""",
            (change_id, project_id, change_type, risk, description_sha256, evidence_sha256,
             request_sha256, requested_by.strip(), now),
        )
        return dict(self.connection.execute("SELECT * FROM change_requests WHERE change_id=?", (change_id,)).fetchone())

    def decide_change_request(self, change_id: str, decision: str, decided_by: str, reason_sha256: str) -> dict:
        request = self.connection.execute("SELECT * FROM change_requests WHERE change_id=?", (change_id,)).fetchone()
        if request is None:
            raise KeyError(change_id)
        if request["status"] != "pending" or decision not in {"approved", "rejected"}:
            raise ValueError("only pending changes accept approved or rejected decisions")
        if decided_by != "owner" or not re.fullmatch(r"[0-9a-f]{64}", reason_sha256):
            raise ValueError("change decision requires owner and a lowercase SHA-256 reason")
        now = utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute("UPDATE change_requests SET status=? WHERE change_id=?", (decision, change_id))
            self.connection.execute(
                """INSERT INTO change_decisions(change_id,decision,decided_by,reason_sha256,request_sha256,decided_at)
                   VALUES(?,?,?,?,?,?)""",
                (change_id, decision, decided_by, reason_sha256, request["request_sha256"], now),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return dict(self.connection.execute("SELECT * FROM change_requests WHERE change_id=?", (change_id,)).fetchone())

    def governance_status_public(self) -> dict:
        def counts(column: str, values: tuple[str, ...]) -> dict:
            result = {value: 0 for value in values}
            for row in self.connection.execute(f"SELECT {column},count(*) FROM project_governance GROUP BY {column}"):
                result[row[0]] = int(row[1])
            return result
        requirements = {value: 0 for value in ("gap", "evidenced", "waived")}
        for row in self.connection.execute("SELECT status,count(*) FROM governance_requirements GROUP BY status"):
            requirements[row[0]] = int(row[1])
        changes = {value: 0 for value in ("pending", "approved", "rejected")}
        for row in self.connection.execute("SELECT status,count(*) FROM change_requests GROUP BY status"):
            changes[row[0]] = int(row[1])
        artifacts = {value: 0 for value in ("pending", "accepted", "rejected")}
        for row in self.connection.execute("SELECT status,count(*) FROM governance_artifacts GROUP BY status"):
            artifacts[row[0]] = int(row[1])
        return {
            "profiles": counts("profile", ("experiment", "business")),
            "classifications": counts("classification", ("internal", "confidential", "restricted")),
            "statuses": counts("status", ("draft", "active", "blocked")),
            "requirements": requirements,
            "changes": changes,
            "artifacts": artifacts,
            "compliance_claims": "prohibited",
        }

    def submit_governance_artifact(self, project_id: str, artifact_type: str,
                                   artifact_sha256: str, submitted_by: str) -> dict:
        allowed = {"threat_model", "security_review", "privacy_review", "vendor_assessment",
                   "incident_runbook", "handover"}
        if artifact_type not in allowed:
            raise ValueError("invalid governance artifact type")
        if not submitted_by.strip() or not re.fullmatch(r"[0-9a-f]{64}", artifact_sha256):
            raise ValueError("governance artifact requires actor and lowercase SHA-256")
        if self.connection.execute("SELECT 1 FROM project_governance WHERE project_id=?", (project_id,)).fetchone() is None:
            raise ValueError("project governance does not exist")
        artifact_id, now = f"GART-{uuid.uuid4().hex.upper()}", utc_now()
        self.connection.execute(
            """INSERT INTO governance_artifacts(artifact_id,project_id,artifact_type,artifact_sha256,status,submitted_by,created_at)
               VALUES(?,?,?,?,'pending',?,?)""",
            (artifact_id, project_id, artifact_type, artifact_sha256, submitted_by.strip(), now),
        )
        return dict(self.connection.execute("SELECT * FROM governance_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone())

    def decide_governance_artifact(self, artifact_id: str, decision: str,
                                   decided_by: str, reason_sha256: str) -> dict:
        artifact = self.connection.execute("SELECT * FROM governance_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
        if artifact is None:
            raise KeyError(artifact_id)
        if artifact["status"] != "pending" or decision not in {"accepted", "rejected"}:
            raise ValueError("only pending artifacts accept accepted or rejected decisions")
        if decided_by != "owner" or not re.fullmatch(r"[0-9a-f]{64}", reason_sha256):
            raise ValueError("artifact decision requires owner and a lowercase SHA-256 reason")
        now = utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute("UPDATE governance_artifacts SET status=? WHERE artifact_id=?", (decision, artifact_id))
            self.connection.execute(
                """INSERT INTO governance_artifact_decisions(artifact_id,decision,decided_by,reason_sha256,decided_at)
                   VALUES(?,?,?,?,?)""", (artifact_id, decision, decided_by, reason_sha256, now),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return dict(self.connection.execute("SELECT * FROM governance_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone())

    def propose_workflow_pattern(self, name: str, source_type: str, definition_sha256: str,
                                 risk: str, requires_external_effects: bool, created_by: str) -> dict:
        if not name.strip() or len(name.strip()) > 120 or not created_by.strip():
            raise ValueError("workflow pattern requires bounded name and actor")
        if source_type not in {"local", "skill", "plugin", "mcp", "n8n"}:
            raise ValueError("invalid workflow source type")
        if risk not in {"low", "medium", "high", "critical"}:
            raise ValueError("invalid workflow risk")
        if not re.fullmatch(r"[0-9a-f]{64}", definition_sha256):
            raise ValueError("workflow definition requires lowercase SHA-256")
        pattern_id, now = f"WFP-{uuid.uuid4().hex.upper()}", utc_now()
        self.connection.execute(
            """INSERT INTO workflow_patterns(pattern_id,name,source_type,definition_sha256,risk,
                   requires_external_effects,status,created_by,created_at,updated_at)
               VALUES(?,?,?,?,?,?,'candidate',?,?,?)""",
            (pattern_id, name.strip(), source_type, definition_sha256, risk,
             int(requires_external_effects), created_by.strip(), now, now),
        )
        return dict(self.connection.execute("SELECT * FROM workflow_patterns WHERE pattern_id=?", (pattern_id,)).fetchone())

    def decide_workflow_pattern(self, pattern_id: str, decision: str,
                                decided_by: str, evidence_sha256: str) -> dict:
        pattern = self.connection.execute("SELECT * FROM workflow_patterns WHERE pattern_id=?", (pattern_id,)).fetchone()
        if pattern is None:
            raise KeyError(pattern_id)
        if pattern["status"] != "candidate" or decision not in {"approved", "rejected"}:
            raise ValueError("only candidate patterns accept approved or rejected decisions")
        if decided_by != "owner" or not re.fullmatch(r"[0-9a-f]{64}", evidence_sha256):
            raise ValueError("workflow decision requires owner and lowercase SHA-256 evidence")
        now = utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                "UPDATE workflow_patterns SET status=?,updated_at=? WHERE pattern_id=?",
                (decision, now, pattern_id),
            )
            self.connection.execute(
                """INSERT INTO workflow_pattern_decisions(pattern_id,decision,decided_by,evidence_sha256,decided_at)
                   VALUES(?,?,?,?,?)""", (pattern_id, decision, decided_by, evidence_sha256, now),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return dict(self.connection.execute("SELECT * FROM workflow_patterns WHERE pattern_id=?", (pattern_id,)).fetchone())

    def workflow_status_public(self) -> dict:
        statuses = {value: 0 for value in ("candidate", "approved", "rejected", "retired")}
        for row in self.connection.execute("SELECT status,count(*) FROM workflow_patterns GROUP BY status"):
            statuses[row[0]] = int(row[1])
        sources = {value: 0 for value in ("local", "skill", "plugin", "mcp", "n8n")}
        for row in self.connection.execute("SELECT source_type,count(*) FROM workflow_patterns GROUP BY source_type"):
            sources[row[0]] = int(row[1])
        gated = self.connection.execute(
            "SELECT count(*) FROM workflow_patterns WHERE requires_external_effects=1"
        ).fetchone()[0]
        plans = {value: 0 for value in ("ready", "approval_required")}
        for row in self.connection.execute("SELECT status,count(*) FROM workflow_plans GROUP BY status"):
            plans[row[0]] = int(row[1])
        tool_statuses = {value: 0 for value in ("observed", "approved", "rejected")}
        for row in self.connection.execute("SELECT status,count(*) FROM automation_tools GROUP BY status"):
            tool_statuses[row[0]] = int(row[1])
        tool_effects = {value: 0 for value in ("none", "read", "write", "destructive")}
        for row in self.connection.execute("SELECT effect_class,count(*) FROM automation_tools GROUP BY effect_class"):
            tool_effects[row[0]] = int(row[1])
        return {"statuses": statuses, "sources": sources, "external_effect_patterns": int(gated),
                "plans": plans, "tool_statuses": tool_statuses, "tool_effects": tool_effects,
                "execution": "disabled"}

    def create_workflow_plan(self, pattern_id: str, input_sha256: str, created_by: str) -> dict:
        pattern = self.connection.execute("SELECT * FROM workflow_patterns WHERE pattern_id=?", (pattern_id,)).fetchone()
        if pattern is None:
            raise KeyError(pattern_id)
        if pattern["status"] != "approved":
            raise ValueError("only approved workflow patterns can be planned")
        if not created_by.strip() or not re.fullmatch(r"[0-9a-f]{64}", input_sha256):
            raise ValueError("workflow plan requires actor and lowercase SHA-256 input")
        canonical = json.dumps({"pattern_id": pattern_id, "definition_sha256": pattern["definition_sha256"],
                                "input_sha256": input_sha256}, sort_keys=True, separators=(",", ":"))
        plan_sha256 = hashlib.sha256(canonical.encode()).hexdigest()
        status = "approval_required" if pattern["requires_external_effects"] else "ready"
        plan_id, now = f"WPL-{uuid.uuid4().hex.upper()}", utc_now()
        self.connection.execute(
            """INSERT INTO workflow_plans(plan_id,pattern_id,input_sha256,plan_sha256,status,created_by,created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (plan_id, pattern_id, input_sha256, plan_sha256, status, created_by.strip(), now),
        )
        return dict(self.connection.execute("SELECT * FROM workflow_plans WHERE plan_id=?", (plan_id,)).fetchone())

    def observe_automation_tool(self, name: str, protocol: str, capability: str,
                                effect_class: str, descriptor_sha256: str, observed_by: str) -> dict:
        if not name.strip() or len(name.strip()) > 120 or not capability.strip() or len(capability.strip()) > 120:
            raise ValueError("automation tool requires bounded name and capability")
        if protocol not in {"local", "mcp", "http"}:
            raise ValueError("invalid automation tool protocol")
        if effect_class not in {"none", "read", "write", "destructive"}:
            raise ValueError("invalid automation tool effect class")
        if not observed_by.strip() or not re.fullmatch(r"[0-9a-f]{64}", descriptor_sha256):
            raise ValueError("automation tool requires actor and lowercase SHA-256 descriptor")
        tool_id, now = f"ATL-{uuid.uuid4().hex.upper()}", utc_now()
        self.connection.execute(
            """INSERT INTO automation_tools(tool_id,name,protocol,capability,effect_class,descriptor_sha256,status,observed_by,created_at)
               VALUES(?,?,?,?,?,?,'observed',?,?)""",
            (tool_id, name.strip(), protocol, capability.strip(), effect_class,
             descriptor_sha256, observed_by.strip(), now),
        )
        return dict(self.connection.execute("SELECT * FROM automation_tools WHERE tool_id=?", (tool_id,)).fetchone())

    def decide_automation_tool(self, tool_id: str, decision: str,
                               decided_by: str, evidence_sha256: str) -> dict:
        tool = self.connection.execute("SELECT * FROM automation_tools WHERE tool_id=?", (tool_id,)).fetchone()
        if tool is None:
            raise KeyError(tool_id)
        if tool["status"] != "observed" or decision not in {"approved", "rejected"}:
            raise ValueError("only observed tools accept approved or rejected decisions")
        if decided_by != "owner" or not re.fullmatch(r"[0-9a-f]{64}", evidence_sha256):
            raise ValueError("tool decision requires owner and lowercase SHA-256 evidence")
        now = utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute("UPDATE automation_tools SET status=? WHERE tool_id=?", (decision, tool_id))
            self.connection.execute(
                """INSERT INTO automation_tool_decisions(tool_id,decision,decided_by,evidence_sha256,decided_at)
                   VALUES(?,?,?,?,?)""", (tool_id, decision, decided_by, evidence_sha256, now),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return dict(self.connection.execute("SELECT * FROM automation_tools WHERE tool_id=?", (tool_id,)).fetchone())

    def create_video_project(self, project_id: str, brief_sha256: str, created_by: str) -> dict:
        if self.connection.execute("SELECT 1 FROM project_governance WHERE project_id=?", (project_id,)).fetchone() is None:
            raise ValueError("project governance does not exist")
        if not created_by.strip() or not re.fullmatch(r"[0-9a-f]{64}", brief_sha256):
            raise ValueError("video project requires actor and lowercase SHA-256 brief")
        video_id, now = f"VID-{uuid.uuid4().hex.upper()}", utc_now()
        stages = ("inspiration", "story", "scene_plan", "media", "narration", "composition", "render", "review", "publish")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                """INSERT INTO video_projects(video_id,project_id,brief_sha256,status,created_by,created_at,updated_at)
                   VALUES(?,?,?,'draft',?,?,?)""", (video_id, project_id, brief_sha256, created_by.strip(), now, now),
            )
            self.connection.executemany(
                "INSERT INTO video_stage_records(video_id,stage,sequence_no,status) VALUES(?,?,?,'pending')",
                [(video_id, stage, index) for index, stage in enumerate(stages, start=1)],
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return dict(self.connection.execute("SELECT * FROM video_projects WHERE video_id=?", (video_id,)).fetchone())

    def complete_video_stage(self, video_id: str, stage: str, evidence_sha256: str, completed_by: str) -> dict:
        if stage == "publish":
            raise ValueError("publishing requires a separate external side-effect approval and is not implemented")
        project = self.connection.execute("SELECT * FROM video_projects WHERE video_id=?", (video_id,)).fetchone()
        if project is None:
            raise KeyError(video_id)
        if project["status"] in {"approved", "cancelled"}:
            raise ValueError("video project no longer accepts production stages")
        if not completed_by.strip() or not re.fullmatch(r"[0-9a-f]{64}", evidence_sha256):
            raise ValueError("stage completion requires actor and lowercase SHA-256 evidence")
        record = self.connection.execute(
            "SELECT * FROM video_stage_records WHERE video_id=? AND stage=?", (video_id, stage)
        ).fetchone()
        if record is None or record["status"] != "pending":
            raise ValueError("video stage is missing or already completed")
        preceding = self.connection.execute(
            "SELECT count(*) FROM video_stage_records WHERE video_id=? AND sequence_no<? AND status!='completed'",
            (video_id, record["sequence_no"]),
        ).fetchone()[0]
        if preceding:
            raise ValueError("video stages must complete in order")
        now = utc_now()
        next_status = "review" if stage == "review" else "in_production"
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                """UPDATE video_stage_records SET status='completed',evidence_sha256=?,completed_by=?,completed_at=?
                   WHERE video_id=? AND stage=? AND status='pending'""",
                (evidence_sha256, completed_by.strip(), now, video_id, stage),
            )
            self.connection.execute("UPDATE video_projects SET status=?,updated_at=? WHERE video_id=?", (next_status, now, video_id))
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return dict(self.connection.execute("SELECT * FROM video_stage_records WHERE video_id=? AND stage=?", (video_id, stage)).fetchone())

    def approve_video_project(self, video_id: str, review_sha256: str, approved_by: str) -> dict:
        project = self.connection.execute("SELECT * FROM video_projects WHERE video_id=?", (video_id,)).fetchone()
        if project is None:
            raise KeyError(video_id)
        if project["status"] != "review" or approved_by != "owner" or not re.fullmatch(r"[0-9a-f]{64}", review_sha256):
            raise ValueError("video approval requires owner, review state, and lowercase SHA-256 evidence")
        now = utc_now()
        self.connection.execute(
            "UPDATE video_projects SET status='approved',approved_by=?,review_sha256=?,updated_at=? WHERE video_id=?",
            (approved_by, review_sha256, now, video_id),
        )
        return dict(self.connection.execute("SELECT * FROM video_projects WHERE video_id=?", (video_id,)).fetchone())

    def video_status_public(self) -> dict:
        projects = {value: 0 for value in ("draft", "in_production", "review", "approved", "cancelled")}
        for row in self.connection.execute("SELECT status,count(*) FROM video_projects GROUP BY status"):
            projects[row[0]] = int(row[1])
        stages = {value: 0 for value in ("pending", "completed")}
        for row in self.connection.execute("SELECT status,count(*) FROM video_stage_records GROUP BY status"):
            stages[row[0]] = int(row[1])
        return {"projects": projects, "stages": stages, "publishing": "approval_gated_unavailable"}

    def register_identity_provider(self, provider_id: str, provider_type: str, issuer: str,
                                   client_id_sha256: str | None, created_by: str) -> dict:
        if created_by != "owner" or not re.fullmatch(r"[a-z][a-z0-9_.-]{2,63}", provider_id):
            raise ValueError("identity provider registration requires owner and bounded ID")
        if provider_type not in {"local_dev", "google_oidc", "oidc"}:
            raise ValueError("invalid identity provider type")
        development_only = provider_type == "local_dev"
        if development_only:
            if issuer != "mosh://local-development" or client_id_sha256 is not None:
                raise ValueError("local identity is fixed and development-only")
        elif not issuer.startswith("https://") or not client_id_sha256 or not re.fullmatch(r"[0-9a-f]{64}", client_id_sha256):
            raise ValueError("OIDC identity requires HTTPS issuer and client ID digest")
        now = utc_now()
        self.connection.execute(
            """INSERT INTO identity_providers(provider_id,provider_type,issuer,client_id_sha256,development_only,status,created_by,created_at)
               VALUES(?,?,?,?,?,'configured',?,?)""",
            (provider_id, provider_type, issuer, client_id_sha256, int(development_only), created_by, now),
        )
        return dict(self.connection.execute("SELECT * FROM identity_providers WHERE provider_id=?", (provider_id,)).fetchone())

    def register_identity_principal(self, provider_id: str, subject_sha256: str,
                                    display_alias: str, actor: str) -> dict:
        if actor != "owner" or not display_alias.strip() or len(display_alias.strip()) > 80:
            raise ValueError("principal registration requires owner and bounded alias")
        if not re.fullmatch(r"[0-9a-f]{64}", subject_sha256):
            raise ValueError("principal subject requires lowercase SHA-256")
        if self.connection.execute("SELECT 1 FROM identity_providers WHERE provider_id=? AND status='configured'", (provider_id,)).fetchone() is None:
            raise ValueError("configured identity provider does not exist")
        principal_id, now = f"IDP-{uuid.uuid4().hex.upper()}", utc_now()
        self.connection.execute(
            """INSERT INTO identity_principals(principal_id,provider_id,subject_sha256,display_alias,status,created_at)
               VALUES(?,?,?,?,'active',?)""", (principal_id, provider_id, subject_sha256, display_alias.strip(), now),
        )
        return dict(self.connection.execute("SELECT * FROM identity_principals WHERE principal_id=?", (principal_id,)).fetchone())

    def grant_workspace_role(self, principal_id: str, role: str, granted_by: str) -> dict:
        if granted_by != "owner" or role not in {"owner", "admin", "developer", "operator", "client", "viewer"}:
            raise ValueError("workspace role grant requires owner and valid role")
        if self.connection.execute("SELECT 1 FROM identity_principals WHERE principal_id=? AND status='active'", (principal_id,)).fetchone() is None:
            raise ValueError("active principal does not exist")
        self.connection.execute(
            "INSERT INTO workspace_role_bindings(principal_id,role,granted_by,created_at) VALUES(?,?,?,?)",
            (principal_id, role, granted_by, utc_now()),
        )
        return dict(self.connection.execute(
            "SELECT * FROM workspace_role_bindings WHERE principal_id=? AND role=?", (principal_id, role)
        ).fetchone())

    def identity_status_public(self) -> dict:
        providers = {value: 0 for value in ("local_dev", "google_oidc", "oidc")}
        for row in self.connection.execute("SELECT provider_type,count(*) FROM identity_providers WHERE status='configured' GROUP BY provider_type"):
            providers[row[0]] = int(row[1])
        roles = {value: 0 for value in ("owner", "admin", "developer", "operator", "client", "viewer")}
        for row in self.connection.execute("SELECT role,count(*) FROM workspace_role_bindings GROUP BY role"):
            roles[row[0]] = int(row[1])
        principals = int(self.connection.execute("SELECT count(*) FROM identity_principals WHERE status='active'").fetchone()[0])
        return {"providers": providers, "active_principals": principals, "roles": roles,
                "local_identity": "development_only"}

    def record_plugin_connection_check(self, plugin_id: str, status: str, health_status: str,
                                       granted_scopes: list[str]) -> dict:
        current = self.get_plugin_public(plugin_id)
        if status not in {"disconnected", "connected", "degraded"}:
            raise ValueError("connection check status must be disconnected, connected, or degraded")
        if health_status not in {"healthy", "degraded", "unavailable"}:
            raise ValueError("connection check requires an observed health state")
        if status == "connected" and health_status != "healthy":
            raise ValueError("connected status requires healthy verification")
        if len(set(granted_scopes)) != len(granted_scopes) or not set(granted_scopes).issubset(current["requested_scopes"]):
            raise ValueError("granted scopes must be a unique subset of requested scopes")
        now = utc_now()
        self.connection.execute(
            """UPDATE plugin_registrations
               SET status=?,health_status=?,granted_scopes_json=?,last_checked_at=?,updated_at=?
               WHERE plugin_id=?""",
            (status, health_status, json.dumps(granted_scopes, separators=(",", ":")), now, now, plugin_id),
        )
        return self.get_plugin_public(plugin_id)

    def allow_plugin_agent(self, plugin_id: str, agent_id: str, allowed_by: str) -> dict:
        plugin = self.get_plugin_public(plugin_id)
        if allowed_by != "owner":
            raise ValueError("plugin agent access requires owner authorization")
        if plugin["status"] != "connected" or plugin["health_status"] != "healthy":
            raise ValueError("plugin must be connected and healthy before agent access")
        if not plugin["granted_scopes"]:
            raise ValueError("plugin must have at least one verified granted scope")
        if self.connection.execute("SELECT 1 FROM agents WHERE agent_id=?", (agent_id,)).fetchone() is None:
            raise ValueError("allowed agent must be registered")
        self.connection.execute(
            """INSERT INTO plugin_agent_access(plugin_id,agent_id,allowed_by,created_at)
               VALUES(?,?,?,?) ON CONFLICT(plugin_id,agent_id) DO NOTHING""",
            (plugin_id, agent_id, allowed_by, utc_now()),
        )
        return self.get_plugin_public(plugin_id)

    def get_account(self, account_id: str) -> Account | None:
        row = self.connection.execute("SELECT * FROM accounts WHERE account_id=?", (account_id,)).fetchone()
        if row is None:
            return None
        return Account(
            account_id=row["account_id"], provider=row["provider"], alias=row["alias"],
            credential_ref=row["credential_ref"], status=AccountStatus(row["status"]),
            metadata=json.loads(row["metadata_json"]), last_verified_at=row["last_verified_at"],
        )

    def list_accounts_public(self) -> list[dict]:
        rows = self.connection.execute(
            "SELECT account_id,provider,alias,status,last_verified_at FROM accounts ORDER BY provider,alias"
        ).fetchall()
        return [dict(row) for row in rows]

    def list_agents_public(self) -> list[dict]:
        rows = self.connection.execute(
            "SELECT agent_id,name,adapter,status,capabilities_json FROM agents ORDER BY name"
        ).fetchall()
        return [
            {"agent_id": row["agent_id"], "name": row["name"], "adapter": row["adapter"],
             "status": row["status"], "capabilities": json.loads(row["capabilities_json"])}
            for row in rows
        ]

    def create_task(self, task: TaskEnvelope) -> TaskEnvelope:
        if task.account_id:
            account = self.connection.execute(
                "SELECT provider,status FROM accounts WHERE account_id=?", (task.account_id,)
            ).fetchone()
            if account is None:
                raise ValueError(f"unknown account_id: {task.account_id}")
            if account["provider"] != task.provider:
                raise ValueError("task provider does not match selected account")
            if account["status"] != AccountStatus.READY.value:
                raise ValueError("selected account is not ready")
        now = utc_now()
        stored = replace(task, created_at=now, updated_at=now)
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute(
                """INSERT INTO tasks(task_id,project_id,from_actor,to_agent,provider,account_id,objective,
                   inputs_json,acceptance_json,constraints_json,risk,approval_required,status,version,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (stored.task_id, stored.project_id, stored.from_actor, stored.to, stored.provider, stored.account_id,
                 stored.objective, json.dumps(stored.inputs), json.dumps(stored.acceptance), json.dumps(stored.constraints),
                 stored.risk.value, int(stored.approval_required), stored.status.value, stored.version, now, now),
            )
            self._event(stored.task_id, "task.created", None, stored.status, {})
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return stored

    def get_task(self, task_id: str) -> TaskEnvelope | None:
        row = self.connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            return None
        return TaskEnvelope(
            task_id=row["task_id"], project_id=row["project_id"], from_actor=row["from_actor"],
            to=row["to_agent"], provider=row["provider"], account_id=row["account_id"], objective=row["objective"],
            inputs=tuple(json.loads(row["inputs_json"])), acceptance=tuple(json.loads(row["acceptance_json"])),
            constraints=tuple(json.loads(row["constraints_json"])), risk=Risk(row["risk"]),
            approval_required=bool(row["approval_required"]), status=TaskStatus(row["status"]),
            created_at=row["created_at"], updated_at=row["updated_at"], version=row["version"],
            cancellation_requested=bool(row["cancellation_requested"]), cancellation_reason=row["cancellation_reason"],
        )

    def transition(self, task_id: str, target: TaskStatus, payload: dict | None = None) -> TaskEnvelope:
        current = self.get_task(task_id)
        if current is None:
            raise KeyError(task_id)
        if target not in ALLOWED_TRANSITIONS[current.status]:
            raise ValueError(f"invalid transition: {current.status.value} -> {target.value}")
        now = utc_now()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            cursor = self.connection.execute(
                "UPDATE tasks SET status=?,version=version+1,updated_at=? WHERE task_id=? AND version=?",
                (target.value, now, task_id, current.version),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("concurrent task update detected")
            self._event(task_id, "task.transitioned", current.status, target, payload or {})
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        updated = self.get_task(task_id)
        assert updated is not None
        return updated

    def enqueue_run(self, task_id: str) -> TaskRun:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        if not task.account_id:
            raise ValueError("task has no selected account")
        if task.status == TaskStatus.DRAFT:
            task = self.transition(task_id, TaskStatus.QUEUED)
        if task.status != TaskStatus.QUEUED:
            raise ValueError("only queued tasks can create a run")
        row = self.connection.execute("SELECT coalesce(max(attempt),0)+1 FROM task_runs WHERE task_id=?", (task_id,)).fetchone()
        attempt = int(row[0])
        run_id = f"RUN-{uuid.uuid4()}"
        now = utc_now()
        self.connection.execute(
            "INSERT INTO task_runs(run_id,task_id,attempt,account_id,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (run_id, task_id, attempt, task.account_id, RunStatus.QUEUED.value, now, now),
        )
        self._event(task_id, "run.queued", task.status, task.status, {"run_id": run_id, "attempt": attempt})
        if task.approval_required:
            self.request_approval(task_id, "execute", task.from_actor)
        return self.get_run(run_id)  # type: ignore[return-value]

    def request_approval(self, task_id: str, scope: str, requested_by: str) -> Approval:
        if scope != "execute":
            raise ValueError("unsupported approval scope")
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        existing = self.connection.execute(
            "SELECT * FROM approvals WHERE task_id=? AND scope=?", (task_id, scope)
        ).fetchone()
        if existing:
            return self._approval_from_row(existing)
        approval_id = f"APR-{uuid.uuid4()}"
        now = utc_now()
        self.connection.execute(
            "INSERT INTO approvals(approval_id,task_id,scope,requested_by,decision,created_at) VALUES(?,?,?,?,?,?)",
            (approval_id, task_id, scope, requested_by, ApprovalDecision.PENDING.value, now),
        )
        self._event(task_id, "approval.requested", task.status, task.status,
                    {"approval_id": approval_id, "scope": scope, "requested_by": requested_by})
        return self.get_approval(approval_id)  # type: ignore[return-value]

    def get_approval(self, approval_id: str) -> Approval | None:
        row = self.connection.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
        return self._approval_from_row(row) if row else None

    def list_approvals(self, task_id: str) -> list[Approval]:
        rows = self.connection.execute(
            "SELECT * FROM approvals WHERE task_id=? ORDER BY created_at", (task_id,)
        ).fetchall()
        return [self._approval_from_row(row) for row in rows]

    def list_approvals_page(self, limit: int = 50, offset: int = 0, decision: str | None = None) -> tuple[list[Approval], int]:
        if limit < 1 or limit > 100 or offset < 0:
            raise ValueError("limit must be 1..100 and offset must be non-negative")
        if decision is not None and decision not in {item.value for item in ApprovalDecision}:
            raise ValueError("invalid approval decision")
        where, parameters = (" WHERE decision=?", [decision]) if decision else ("", [])
        rows = self.connection.execute(
            f"SELECT * FROM approvals{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*parameters, limit, offset),
        ).fetchall()
        total = int(self.connection.execute(f"SELECT count(*) FROM approvals{where}", parameters).fetchone()[0])
        return [self._approval_from_row(row) for row in rows], total

    def decide_approval(self, task_id: str, approved: bool, decided_by: str, reason: str) -> Approval:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        row = self.connection.execute(
            "SELECT * FROM approvals WHERE task_id=? AND scope='execute'", (task_id,)
        ).fetchone()
        if row is None:
            raise ValueError("task has no pending execute approval")
        if row["decision"] != ApprovalDecision.PENDING.value:
            raise ValueError("approval decision is immutable")
        decision = ApprovalDecision.APPROVED if approved else ApprovalDecision.REJECTED
        now = utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                "UPDATE approvals SET decision=?,decided_by=?,reason=?,decided_at=? WHERE approval_id=? AND decision=?",
                (decision.value, decided_by, reason, now, row["approval_id"], ApprovalDecision.PENDING.value),
            )
            self._event(task_id, f"approval.{decision.value}", task.status,
                        task.status if approved else TaskStatus.REJECTED,
                        {"approval_id": row["approval_id"], "scope": "execute", "decided_by": decided_by, "reason": reason})
            if not approved:
                self.connection.execute(
                    "UPDATE tasks SET status=?,version=version+1,updated_at=? WHERE task_id=? AND status=?",
                    (TaskStatus.REJECTED.value, now, task_id, TaskStatus.QUEUED.value),
                )
                self.connection.execute(
                    "UPDATE task_runs SET status=?,updated_at=? WHERE task_id=? AND status=?",
                    (RunStatus.CANCELLED.value, now, task_id, RunStatus.QUEUED.value),
                )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get_approval(row["approval_id"])  # type: ignore[return-value]

    def request_side_effect(self, task_id: str, action: str, target: str, parameters: dict,
                            risk: Risk, requested_by: str, expires_at: str) -> SideEffectRequest:
        if self.get_task(task_id) is None:
            raise KeyError(task_id)
        digest = side_effect_digest(action, target, parameters)
        request_id = f"SFX-{uuid.uuid4()}"
        now = utc_now()
        self.connection.execute(
            """INSERT INTO side_effect_requests(request_id,task_id,action,target,parameters_json,risk,
               request_digest,status,requested_by,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (request_id, task_id, action, target, json.dumps(parameters, sort_keys=True, separators=(",", ":")),
             risk.value, digest, SideEffectStatus.PENDING.value, requested_by, expires_at, now),
        )
        task = self.get_task(task_id)
        self._event(task_id, "side_effect.requested", task.status, task.status,
                    {"request_id": request_id, "action": action, "target": target,
                     "request_digest": digest, "risk": risk.value, "expires_at": expires_at})
        return self.get_side_effect(request_id)  # type: ignore[return-value]

    def get_side_effect(self, request_id: str) -> SideEffectRequest | None:
        row = self.connection.execute(
            "SELECT * FROM side_effect_requests WHERE request_id=?", (request_id,)
        ).fetchone()
        return self._side_effect_from_row(row) if row else None

    def list_side_effects(self, task_id: str) -> list[SideEffectRequest]:
        rows = self.connection.execute(
            "SELECT * FROM side_effect_requests WHERE task_id=? ORDER BY created_at", (task_id,)
        ).fetchall()
        return [self._side_effect_from_row(row) for row in rows]

    def list_side_effects_page(self, limit: int = 50, offset: int = 0, status: str | None = None) -> tuple[list[dict], int]:
        if limit < 1 or limit > 100 or offset < 0:
            raise ValueError("limit must be 1..100 and offset must be non-negative")
        if status is not None and status not in {item.value for item in SideEffectStatus}:
            raise ValueError("invalid side-effect status")
        where, parameters = (" WHERE status=?", [status]) if status else ("", [])
        rows = self.connection.execute(
            f"""SELECT request_id,task_id,action,risk,status,requested_by,decided_by,reason,
                       expires_at,created_at,decided_at,consumed_at
                  FROM side_effect_requests{where} ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (*parameters, limit, offset),
        ).fetchall()
        total = int(self.connection.execute(f"SELECT count(*) FROM side_effect_requests{where}", parameters).fetchone()[0])
        return [dict(row) for row in rows], total

    def decide_side_effect(self, request_id: str, request_digest: str, approved: bool,
                           decided_by: str, reason: str) -> SideEffectRequest:
        request = self.get_side_effect(request_id)
        if request is None:
            raise KeyError(request_id)
        if request.status != SideEffectStatus.PENDING:
            raise ValueError("side-effect decision is immutable")
        if request.request_digest != request_digest:
            raise ValueError("request digest mismatch")
        if datetime.fromisoformat(request.expires_at.replace("Z", "+00:00")) <= datetime.now(UTC):
            raise ValueError("side-effect request expired")
        status = SideEffectStatus.APPROVED if approved else SideEffectStatus.REJECTED
        now = utc_now()
        self.connection.execute(
            "UPDATE side_effect_requests SET status=?,decided_by=?,reason=?,decided_at=? WHERE request_id=? AND status=?",
            (status.value, decided_by, reason, now, request_id, SideEffectStatus.PENDING.value),
        )
        task = self.get_task(request.task_id)
        self._event(request.task_id, f"side_effect.{status.value}", task.status, task.status,
                    {"request_id": request_id, "request_digest": request_digest,
                     "decided_by": decided_by, "reason": reason})
        return self.get_side_effect(request_id)  # type: ignore[return-value]

    def consume_side_effect(self, request_id: str, action: str, target: str, parameters: dict) -> SideEffectRequest:
        request = self.get_side_effect(request_id)
        if request is None:
            raise KeyError(request_id)
        if request.status != SideEffectStatus.APPROVED:
            raise ValueError("side-effect request is not approved or was already consumed")
        if request.request_digest != side_effect_digest(action, target, parameters):
            raise ValueError("approved parameters do not match requested side effect")
        if datetime.fromisoformat(request.expires_at.replace("Z", "+00:00")) <= datetime.now(UTC):
            raise ValueError("side-effect approval expired")
        now = utc_now()
        cursor = self.connection.execute(
            "UPDATE side_effect_requests SET status=?,consumed_at=? WHERE request_id=? AND status=?",
            (SideEffectStatus.CONSUMED.value, now, request_id, SideEffectStatus.APPROVED.value),
        )
        if cursor.rowcount != 1:
            raise ValueError("side-effect approval replay detected")
        task = self.get_task(request.task_id)
        self._event(request.task_id, "side_effect.consumed", task.status, task.status,
                    {"request_id": request_id, "request_digest": request.request_digest})
        return self.get_side_effect(request_id)  # type: ignore[return-value]

    def begin_side_effect_execution(self, request_id: str, action: str, target: str, parameters: dict,
                                    artifact_path: str, artifact_sha256: str) -> SideEffectExecution:
        existing = self.connection.execute(
            "SELECT * FROM side_effect_executions WHERE request_id=?", (request_id,)
        ).fetchone()
        if existing:
            return self._side_effect_execution_from_row(existing)
        request = self.get_side_effect(request_id)
        if request is None:
            raise KeyError(request_id)
        if request.status != SideEffectStatus.APPROVED:
            raise ValueError("side-effect request is not approved")
        if request.request_digest != side_effect_digest(action, target, parameters):
            raise ValueError("approved parameters do not match requested side effect")
        if datetime.fromisoformat(request.expires_at.replace("Z", "+00:00")) <= datetime.now(UTC):
            raise ValueError("side-effect approval expired")
        execution_id = f"SFXE-{uuid.uuid4()}"
        idempotency_key = f"mosh:{request_id}:{request.request_digest}"
        now = utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self.connection.execute(
                "UPDATE side_effect_requests SET status=?,consumed_at=? WHERE request_id=? AND status=?",
                (SideEffectStatus.CONSUMED.value, now, request_id, SideEffectStatus.APPROVED.value),
            )
            if cursor.rowcount != 1:
                raise ValueError("side-effect approval replay detected")
            self.connection.execute(
                """INSERT INTO side_effect_executions(execution_id,request_id,idempotency_key,status,
                   artifact_path,artifact_sha256,prepared_at) VALUES(?,?,?,?,?,?,?)""",
                (execution_id, request_id, idempotency_key, SideEffectExecutionStatus.PREPARED.value,
                 artifact_path, artifact_sha256, now),
            )
            task = self.get_task(request.task_id)
            self._event(request.task_id, "side_effect.execution_prepared", task.status, task.status,
                        {"request_id": request_id, "execution_id": execution_id,
                         "idempotency_key": idempotency_key, "artifact_path": artifact_path})
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get_side_effect_execution(execution_id)  # type: ignore[return-value]

    def get_side_effect_execution(self, execution_id: str) -> SideEffectExecution | None:
        row = self.connection.execute(
            "SELECT * FROM side_effect_executions WHERE execution_id=?", (execution_id,)
        ).fetchone()
        return self._side_effect_execution_from_row(row) if row else None

    def list_side_effect_executions(self, status: SideEffectExecutionStatus | None = None) -> list[SideEffectExecution]:
        if status is None:
            rows = self.connection.execute("SELECT * FROM side_effect_executions ORDER BY prepared_at").fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM side_effect_executions WHERE status=? ORDER BY prepared_at", (status.value,)
            ).fetchall()
        return [self._side_effect_execution_from_row(row) for row in rows]

    def complete_side_effect_execution(self, execution_id: str) -> SideEffectExecution:
        execution = self.get_side_effect_execution(execution_id)
        if execution is None:
            raise KeyError(execution_id)
        if execution.status == SideEffectExecutionStatus.COMPLETED:
            return execution
        if execution.status != SideEffectExecutionStatus.PREPARED:
            raise ValueError("side-effect execution is not recoverable")
        now = utc_now()
        self.connection.execute(
            "UPDATE side_effect_executions SET status=?,completed_at=? WHERE execution_id=? AND status=?",
            (SideEffectExecutionStatus.COMPLETED.value, now, execution_id, SideEffectExecutionStatus.PREPARED.value),
        )
        request = self.get_side_effect(execution.request_id)
        task = self.get_task(request.task_id)
        self._event(request.task_id, "side_effect.execution_completed", task.status, task.status,
                    {"request_id": request.request_id, "execution_id": execution_id,
                     "idempotency_key": execution.idempotency_key})
        return self.get_side_effect_execution(execution_id)  # type: ignore[return-value]

    def fail_side_effect_execution(self, execution_id: str, error: str) -> SideEffectExecution:
        execution = self.get_side_effect_execution(execution_id)
        if execution is None:
            raise KeyError(execution_id)
        if execution.status == SideEffectExecutionStatus.FAILED:
            return execution
        if execution.status != SideEffectExecutionStatus.PREPARED:
            raise ValueError("completed side-effect execution cannot fail")
        now = utc_now()
        self.connection.execute(
            "UPDATE side_effect_executions SET status=?,error=?,failed_at=? WHERE execution_id=? AND status=?",
            (SideEffectExecutionStatus.FAILED.value, error[:500], now, execution_id,
             SideEffectExecutionStatus.PREPARED.value),
        )
        request = self.get_side_effect(execution.request_id)
        task = self.get_task(request.task_id)
        self._event(request.task_id, "side_effect.execution_failed", task.status, task.status,
                    {"request_id": request.request_id, "execution_id": execution_id, "error": error[:500]})
        return self.get_side_effect_execution(execution_id)  # type: ignore[return-value]

    def create_cleanup_plan(self, task_id: str, execution_id: str, source_path: str,
                            trash_path: str, artifact_sha256: str, plan_digest: str,
                            retain_until: str) -> CleanupPlan:
        plan_id = f"CLN-{uuid.uuid4()}"
        now = utc_now()
        self.connection.execute(
            """INSERT INTO cleanup_plans(plan_id,task_id,execution_id,source_path,trash_path,
               artifact_sha256,plan_digest,status,created_at,retain_until) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (plan_id, task_id, execution_id, source_path, trash_path, artifact_sha256,
             plan_digest, CleanupStatus.PLANNED.value, now, retain_until),
        )
        task = self.get_task(task_id)
        self._event(task_id, "cleanup.planned", task.status, task.status,
                    {"plan_id": plan_id, "execution_id": execution_id, "plan_digest": plan_digest})
        return self.get_cleanup_plan(plan_id)  # type: ignore[return-value]

    def get_cleanup_plan(self, plan_id: str) -> CleanupPlan | None:
        row = self.connection.execute("SELECT * FROM cleanup_plans WHERE plan_id=?", (plan_id,)).fetchone()
        return self._cleanup_plan_from_row(row) if row else None

    def list_cleanup_plans(self) -> list[CleanupPlan]:
        return [self._cleanup_plan_from_row(row) for row in self.connection.execute(
            "SELECT * FROM cleanup_plans ORDER BY created_at"
        ).fetchall()]

    def begin_cleanup_operation(self, plan_id: str, request_id: str, action: str) -> CleanupOperation:
        existing = self.connection.execute(
            "SELECT * FROM cleanup_operations WHERE request_id=?", (request_id,)
        ).fetchone()
        if existing:
            return self._cleanup_operation_from_row(existing)
        request = self.get_side_effect(request_id)
        if request is None or request.status != SideEffectStatus.APPROVED:
            raise ValueError("cleanup action has no approved request")
        expected = "trash_marker" if action == "trash" else "restore_marker"
        if request.action != expected or request.target != plan_id:
            raise ValueError("cleanup approval does not match operation")
        operation_id = f"CLNO-{uuid.uuid4()}"
        now = utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self.connection.execute(
                "UPDATE side_effect_requests SET status=?,consumed_at=? WHERE request_id=? AND status=?",
                (SideEffectStatus.CONSUMED.value, now, request_id, SideEffectStatus.APPROVED.value),
            )
            if cursor.rowcount != 1:
                raise ValueError("cleanup approval replay detected")
            self.connection.execute(
                "INSERT INTO cleanup_operations(operation_id,plan_id,request_id,action,status,prepared_at) VALUES(?,?,?,?,?,?)",
                (operation_id, plan_id, request_id, action, CleanupOperationStatus.PREPARED.value, now),
            )
            plan = self.get_cleanup_plan(plan_id)
            task = self.get_task(plan.task_id)
            self._event(plan.task_id, "cleanup.operation_prepared", task.status, task.status,
                        {"plan_id": plan_id, "operation_id": operation_id, "action": action})
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get_cleanup_operation(operation_id)  # type: ignore[return-value]

    def get_cleanup_operation(self, operation_id: str) -> CleanupOperation | None:
        row = self.connection.execute("SELECT * FROM cleanup_operations WHERE operation_id=?", (operation_id,)).fetchone()
        return self._cleanup_operation_from_row(row) if row else None

    def list_cleanup_operations(self, status: CleanupOperationStatus | None = None) -> list[CleanupOperation]:
        sql, params = ("SELECT * FROM cleanup_operations ORDER BY prepared_at", ()) if status is None else (
            "SELECT * FROM cleanup_operations WHERE status=? ORDER BY prepared_at", (status.value,))
        return [self._cleanup_operation_from_row(row) for row in self.connection.execute(sql, params).fetchall()]

    def finish_cleanup_operation(self, operation_id: str, target: CleanupStatus) -> CleanupOperation:
        operation = self.get_cleanup_operation(operation_id)
        plan = self.get_cleanup_plan(operation.plan_id) if operation else None
        if operation is None or plan is None or operation.status != CleanupOperationStatus.PREPARED:
            raise ValueError("cleanup operation is not prepared")
        source = CleanupStatus.PLANNED if operation.action == "trash" else CleanupStatus.TRASHED
        now = utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                "UPDATE cleanup_operations SET status=?,completed_at=? WHERE operation_id=? AND status=?",
                (CleanupOperationStatus.COMPLETED.value, now, operation_id, CleanupOperationStatus.PREPARED.value),
            )
            field = "trashed_at" if target == CleanupStatus.TRASHED else "restored_at"
            cursor = self.connection.execute(
                f"UPDATE cleanup_plans SET status=?,{field}=? WHERE plan_id=? AND status=?",
                (target.value, now, plan.plan_id, source.value),
            )
            if cursor.rowcount != 1:
                raise ValueError("cleanup plan state changed during operation")
            task = self.get_task(plan.task_id)
            self._event(plan.task_id, f"cleanup.{target.value}", task.status, task.status,
                        {"plan_id": plan.plan_id, "operation_id": operation_id})
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get_cleanup_operation(operation_id)  # type: ignore[return-value]

    def fail_cleanup_operation(self, operation_id: str, error: str) -> CleanupOperation:
        now = utc_now()
        self.connection.execute(
            "UPDATE cleanup_operations SET status=?,error=?,failed_at=? WHERE operation_id=? AND status=?",
            (CleanupOperationStatus.FAILED.value, error[:500], now, operation_id, CleanupOperationStatus.PREPARED.value),
        )
        return self.get_cleanup_operation(operation_id)  # type: ignore[return-value]

    def set_cleanup_status(self, plan_id: str, source: CleanupStatus, target: CleanupStatus) -> CleanupPlan:
        field = "trashed_at" if target == CleanupStatus.TRASHED else "restored_at"
        now = utc_now()
        cursor = self.connection.execute(
            f"UPDATE cleanup_plans SET status=?,{field}=? WHERE plan_id=? AND status=?",
            (target.value, now, plan_id, source.value),
        )
        if cursor.rowcount != 1:
            raise ValueError("cleanup plan is not in the expected state")
        plan = self.get_cleanup_plan(plan_id)
        task = self.get_task(plan.task_id)
        self._event(plan.task_id, f"cleanup.{target.value}", task.status, task.status,
                    {"plan_id": plan_id, "plan_digest": plan.plan_digest})
        return plan

    def get_run(self, run_id: str) -> TaskRun | None:
        row = self.connection.execute("SELECT * FROM task_runs WHERE run_id=?", (run_id,)).fetchone()
        return self._run_from_row(row) if row else None

    def claim_next(self, worker_id: str, lease_seconds: int = 60) -> TaskRun | None:
        now = utc_now()
        expires = (datetime.now(UTC) + timedelta(seconds=lease_seconds)).isoformat().replace("+00:00", "Z")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute(
                """SELECT task_runs.* FROM task_runs JOIN tasks USING(task_id)
                   WHERE task_runs.status=? AND (
                     tasks.approval_required=0 OR EXISTS (
                       SELECT 1 FROM approvals WHERE approvals.task_id=tasks.task_id
                       AND approvals.scope='execute' AND approvals.decision='approved'
                     )
                   ) ORDER BY task_runs.created_at,task_runs.attempt LIMIT 1""",
                (RunStatus.QUEUED.value,),
            ).fetchone()
            if row is None:
                self.connection.commit()
                return None
            self.connection.execute(
                "UPDATE task_runs SET status=?,lease_owner=?,lease_expires_at=?,updated_at=? WHERE run_id=? AND status=?",
                (RunStatus.CLAIMED.value, worker_id, expires, now, row["run_id"], RunStatus.QUEUED.value),
            )
            self.connection.execute(
                "UPDATE tasks SET status=?,version=version+1,updated_at=? WHERE task_id=? AND status=?",
                (TaskStatus.CLAIMED.value, now, row["task_id"], TaskStatus.QUEUED.value),
            )
            self._event(row["task_id"], "run.claimed", TaskStatus.QUEUED, TaskStatus.CLAIMED,
                        {"run_id": row["run_id"], "worker_id": worker_id, "lease_expires_at": expires})
            self.connection.commit()
            return self.get_run(row["run_id"])
        except Exception:
            self.connection.rollback()
            raise

    def mark_run_running(self, run_id: str, worker_id: str) -> TaskRun:
        run = self._owned_run(run_id, worker_id, RunStatus.CLAIMED)
        now = utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute("UPDATE task_runs SET status=?,updated_at=? WHERE run_id=?",
                                    (RunStatus.RUNNING.value, now, run_id))
            self.connection.execute("UPDATE tasks SET status=?,version=version+1,updated_at=? WHERE task_id=?",
                                    (TaskStatus.RUNNING.value, now, run.task_id))
            self._event(run.task_id, "run.started", TaskStatus.CLAIMED, TaskStatus.RUNNING, {"run_id": run_id})
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get_run(run_id)  # type: ignore[return-value]

    def record_execution_plan(self, run_id: str, worker_id: str, adapter_name: str,
                              command_argv_json: str, command_env_keys_json: str,
                              evidence_sha256: str) -> TaskRun:
        """Persist a digest of canonical redacted evidence, never an exact-command attestation."""
        run = self._owned_run(run_id, worker_id, RunStatus.RUNNING)
        now = utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self.connection.execute(
                """UPDATE task_runs SET adapter_name=?,command_argv_json=?,command_env_keys_json=?,
                   command_sha256=?,command_recorded_at=?,updated_at=?
                   WHERE run_id=? AND command_recorded_at IS NULL""",
                (adapter_name, command_argv_json, command_env_keys_json, evidence_sha256,
                 now, now, run_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("execution plan is already recorded")
            task = self.get_task(run.task_id)
            self._event(run.task_id, "run.command_recorded", task.status if task else None,
                        task.status if task else None,
                        {"run_id": run_id, "adapter_name": adapter_name,
                         "command_evidence_sha256": evidence_sha256})
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get_run(run_id)  # type: ignore[return-value]

    def complete_run(self, run_id: str, worker_id: str, result: str, completion_key: str | None = None) -> TaskRun:
        completion_key = completion_key or run_id
        existing = self.get_run(run_id)
        if existing and existing.status == RunStatus.COMPLETED and existing.completion_key == completion_key:
            return existing
        run = self._owned_run(run_id, worker_id, RunStatus.RUNNING)
        now = utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                "UPDATE task_runs SET status=?,result=?,completion_key=?,lease_expires_at=NULL,updated_at=? WHERE run_id=?",
                (RunStatus.COMPLETED.value, result, completion_key, now, run_id),
            )
            self.connection.execute("UPDATE tasks SET status=?,version=version+1,updated_at=? WHERE task_id=?",
                                    (TaskStatus.REVIEW.value, now, run.task_id))
            self._event(run.task_id, "run.completed", TaskStatus.RUNNING, TaskStatus.REVIEW, {"run_id": run_id})
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get_run(run_id)  # type: ignore[return-value]

    def renew_lease(self, run_id: str, worker_id: str, lease_seconds: int = 60) -> TaskRun:
        run = self.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.lease_owner != worker_id or run.status not in {RunStatus.CLAIMED, RunStatus.RUNNING}:
            raise ValueError("worker does not own an active run")
        if not run.lease_expires_at or datetime.fromisoformat(run.lease_expires_at.replace("Z", "+00:00")) <= datetime.now(UTC):
            raise ValueError("expired lease cannot be renewed")
        expires = (datetime.now(UTC) + timedelta(seconds=lease_seconds)).isoformat().replace("+00:00", "Z")
        self.connection.execute(
            "UPDATE task_runs SET lease_expires_at=?,updated_at=? WHERE run_id=?",
            (expires, utc_now(), run_id),
        )
        task = self.get_task(run.task_id)
        self._event(run.task_id, "run.lease_renewed", task.status if task else None, task.status if task else None,
                    {"run_id": run_id, "worker_id": worker_id, "lease_expires_at": expires})
        return self.get_run(run_id)  # type: ignore[return-value]

    def request_cancel(self, task_id: str, reason: str) -> TaskEnvelope:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        now = utc_now()
        if task.status in {TaskStatus.DRAFT, TaskStatus.QUEUED, TaskStatus.BLOCKED, TaskStatus.FAILED}:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                self.connection.execute(
                    "UPDATE tasks SET status=?,cancellation_requested=1,cancellation_reason=?,version=version+1,updated_at=? WHERE task_id=?",
                    (TaskStatus.CANCELLED.value, reason, now, task_id),
                )
                self.connection.execute(
                    "UPDATE task_runs SET status=?,updated_at=? WHERE task_id=? AND status=?",
                    (RunStatus.CANCELLED.value, now, task_id, RunStatus.QUEUED.value),
                )
                self._event(task_id, "task.cancelled", task.status, TaskStatus.CANCELLED, {"reason": reason})
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise
        elif task.status in {TaskStatus.CLAIMED, TaskStatus.RUNNING}:
            self.connection.execute(
                "UPDATE tasks SET cancellation_requested=1,cancellation_reason=?,version=version+1,updated_at=? WHERE task_id=?",
                (reason, now, task_id),
            )
            self._event(task_id, "task.cancel.requested", task.status, task.status, {"reason": reason})
        else:
            raise ValueError("task cannot be cancelled in its current state")
        return self.get_task(task_id)  # type: ignore[return-value]

    def cancel_run(self, run_id: str, worker_id: str) -> TaskRun:
        run = self._owned_run(run_id, worker_id, RunStatus.RUNNING)
        task = self.get_task(run.task_id)
        if task is None or not task.cancellation_requested:
            raise ValueError("task has no cancellation request")
        now = utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                "UPDATE task_runs SET status=?,lease_expires_at=NULL,updated_at=? WHERE run_id=?",
                (RunStatus.CANCELLED.value, now, run_id),
            )
            self.connection.execute(
                "UPDATE tasks SET status=?,version=version+1,updated_at=? WHERE task_id=?",
                (TaskStatus.CANCELLED.value, now, run.task_id),
            )
            self._event(run.task_id, "run.cancelled", TaskStatus.RUNNING, TaskStatus.CANCELLED,
                        {"run_id": run_id, "reason": task.cancellation_reason})
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get_run(run_id)  # type: ignore[return-value]

    def fail_run(self, run_id: str, worker_id: str, error: str) -> TaskRun:
        run = self._owned_run(run_id, worker_id, RunStatus.RUNNING)
        now = utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                "UPDATE task_runs SET status=?,error=?,lease_expires_at=NULL,updated_at=? WHERE run_id=?",
                (RunStatus.FAILED.value, error, now, run_id),
            )
            self.connection.execute("UPDATE tasks SET status=?,version=version+1,updated_at=? WHERE task_id=?",
                                    (TaskStatus.FAILED.value, now, run.task_id))
            self._event(run.task_id, "run.failed", TaskStatus.RUNNING, TaskStatus.FAILED,
                        {"run_id": run_id, "error": error[:500]})
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get_run(run_id)  # type: ignore[return-value]

    def recover_expired_runs(self, at: str | None = None) -> list[TaskRun]:
        cutoff = at or utc_now()
        expired = self.connection.execute(
            "SELECT * FROM task_runs WHERE status IN (?,?) AND lease_expires_at IS NOT NULL AND lease_expires_at<=?",
            (RunStatus.CLAIMED.value, RunStatus.RUNNING.value, cutoff),
        ).fetchall()
        replacements: list[TaskRun] = []
        for row in expired:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                now = utc_now()
                self.connection.execute(
                    "UPDATE task_runs SET status=?,lease_expires_at=NULL,updated_at=? WHERE run_id=?",
                    (RunStatus.ABANDONED.value, now, row["run_id"]),
                )
                self.connection.execute(
                    "UPDATE tasks SET status=?,version=version+1,updated_at=? WHERE task_id=?",
                    (TaskStatus.QUEUED.value, now, row["task_id"]),
                )
                new_id = f"RUN-{uuid.uuid4()}"
                next_attempt = int(row["attempt"]) + 1
                self.connection.execute(
                    "INSERT INTO task_runs(run_id,task_id,attempt,account_id,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (new_id, row["task_id"], next_attempt, row["account_id"], RunStatus.QUEUED.value, now, now),
                )
                self._event(row["task_id"], "run.recovered", TaskStatus(row["status"]), TaskStatus.QUEUED,
                            {"abandoned_run_id": row["run_id"], "replacement_run_id": new_id})
                self.connection.commit()
                replacements.append(self.get_run(new_id))  # type: ignore[arg-type]
            except Exception:
                self.connection.rollback()
                raise
        return replacements

    def reroute_task(self, task_id: str, account_id: str, reason: str) -> TaskEnvelope:
        task = self.get_task(task_id)
        account = self.get_account(account_id)
        if task is None:
            raise KeyError(task_id)
        if account is None or account.status != AccountStatus.READY:
            raise ValueError("target account is not ready")
        if task.status not in {TaskStatus.DRAFT, TaskStatus.QUEUED, TaskStatus.BLOCKED, TaskStatus.FAILED}:
            raise ValueError("active or terminal tasks cannot be rerouted")
        old_account = task.account_id
        now = utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                "UPDATE tasks SET provider=?,account_id=?,version=version+1,updated_at=? WHERE task_id=?",
                (account.provider, account.account_id, now, task_id),
            )
            self.connection.execute(
                "UPDATE task_runs SET account_id=?,updated_at=? WHERE task_id=? AND status=?",
                (account.account_id, now, task_id, RunStatus.QUEUED.value),
            )
            self._event(task_id, "task.rerouted", task.status, task.status,
                        {"from_account_id": old_account, "to_account_id": account_id, "reason": reason})
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get_task(task_id)  # type: ignore[return-value]

    def list_events(self, task_id: str) -> list[dict]:
        rows = self.connection.execute(
            "SELECT * FROM task_events WHERE task_id=? ORDER BY event_id", (task_id,)
        ).fetchall()
        return [
            {
                "event_id": row["event_id"],
                "task_id": row["task_id"],
                "event_type": row["event_type"],
                "from_status": row["from_status"],
                "to_status": row["to_status"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def list_events_page(self, task_id: str, after_event_id: int = 0, limit: int = 50) -> tuple[list[dict], bool, int | None]:
        if after_event_id < 0 or limit < 1 or limit > 100:
            raise ValueError("after_event_id must be non-negative and limit must be 1..100")
        rows = self.connection.execute(
            "SELECT * FROM task_events WHERE task_id=? AND event_id>? ORDER BY event_id LIMIT ?",
            (task_id, after_event_id, limit + 1),
        ).fetchall()
        has_next = len(rows) > limit
        rows = rows[:limit]
        events = [
            {
                "event_id": row["event_id"], "task_id": row["task_id"], "event_type": row["event_type"],
                "from_status": row["from_status"], "to_status": row["to_status"],
                "payload": json.loads(row["payload_json"]), "created_at": row["created_at"],
            }
            for row in rows
        ]
        return events, has_next, (events[-1]["event_id"] if events else None)

    def list_tasks(self, limit: int = 50, offset: int = 0) -> tuple[list[TaskEnvelope], int]:
        if limit < 1 or limit > 100 or offset < 0:
            raise ValueError("limit must be 1..100 and offset must be non-negative")
        rows = self.connection.execute(
            "SELECT task_id FROM tasks ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
        total = int(self.connection.execute("SELECT count(*) FROM tasks").fetchone()[0])
        return [self.get_task(row["task_id"]) for row in rows], total  # type: ignore[list-item]

    def list_projects_public(self, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        if limit < 1 or limit > 100 or offset < 0:
            raise ValueError("limit must be 1..100 and offset must be non-negative")
        total = int(self.connection.execute("SELECT count(DISTINCT project_id) FROM tasks").fetchone()[0])
        rows = self.connection.execute(
            """SELECT project_id,
                      count(*) AS total_tasks,
                      sum(CASE WHEN status IN ('queued','claimed','running','blocked') THEN 1 ELSE 0 END) AS active_tasks,
                      sum(CASE WHEN status='review' THEN 1 ELSE 0 END) AS review_tasks,
                      sum(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed_tasks,
                      sum(CASE WHEN status IN ('failed','cancelled','rejected') THEN 1 ELSE 0 END) AS attention_tasks,
                      max(updated_at) AS last_updated
                 FROM tasks
             GROUP BY project_id
             ORDER BY last_updated DESC, project_id ASC
                LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        return [dict(row) for row in rows], total

    def task_snapshot(self, task_id: str) -> dict | None:
        task = self.get_task(task_id)
        if task is None:
            return None
        runs = [self._public_run(self._run_from_row(row)) for row in self.connection.execute(
            "SELECT * FROM task_runs WHERE task_id=? ORDER BY attempt", (task_id,)
        ).fetchall()]
        return {
            "task": task.to_dict(),
            "runs": runs,
            "approvals": [item.to_dict() for item in self.list_approvals(task_id)],
            "side_effects": [item.to_dict() for item in self.list_side_effects(task_id)],
        }

    @staticmethod
    def _public_run(run: TaskRun) -> dict:
        value = run.to_dict()
        for field in ("command_argv_json", "command_env_keys_json", "command_sha256",
                      "command_recorded_at"):
            value.pop(field, None)
        return value

    def summary(self) -> dict[str, int]:
        return {
            "accounts": self.connection.execute("SELECT count(*) FROM accounts").fetchone()[0],
            "agents": self.connection.execute("SELECT count(*) FROM agents").fetchone()[0],
            "tasks": self.connection.execute("SELECT count(*) FROM tasks").fetchone()[0],
            "events": self.connection.execute("SELECT count(*) FROM task_events").fetchone()[0],
            "runs": self.connection.execute("SELECT count(*) FROM task_runs").fetchone()[0],
            "approvals": self.connection.execute("SELECT count(*) FROM approvals").fetchone()[0],
            "side_effect_requests": self.connection.execute("SELECT count(*) FROM side_effect_requests").fetchone()[0],
            "side_effect_executions": self.connection.execute("SELECT count(*) FROM side_effect_executions").fetchone()[0],
            "cleanup_plans": self.connection.execute("SELECT count(*) FROM cleanup_plans").fetchone()[0],
            "cleanup_operations": self.connection.execute("SELECT count(*) FROM cleanup_operations").fetchone()[0],
        }

    def memory_status_public(self) -> dict:
        domains = (
            ("task_context", "tasks", "updated_at"),
            ("execution_history", "task_runs", "updated_at"),
            ("audit_events", "task_events", "created_at"),
            ("approval_history", "approvals", "created_at"),
            ("side_effect_history", "side_effect_requests", "created_at"),
            ("cleanup_history", "cleanup_plans", "created_at"),
        )
        stores = []
        for name, table, timestamp in domains:
            row = self.connection.execute(
                f"SELECT count(*) AS record_count, max({timestamp}) AS last_recorded_at FROM {table}"
            ).fetchone()
            stores.append({
                "name": name,
                "record_count": int(row["record_count"]),
                "last_recorded_at": row["last_recorded_at"],
            })
        schema_version = int(self.connection.execute(
            "SELECT coalesce(max(version), 0) FROM schema_migrations"
        ).fetchone()[0])
        integrity = str(self.connection.execute("PRAGMA quick_check(1)").fetchone()[0])
        return {
            "status": "healthy" if integrity == "ok" else "degraded",
            "schema_version": schema_version,
            "stores": stores,
        }

    def log_status_public(self) -> dict:
        stream_names = ("task", "run", "approval", "side_effect", "cleanup", "other")
        aggregates = {name: {"name": name, "event_count": 0, "last_event_at": None} for name in stream_names}
        signals = {"failures": 0, "cancellations": 0, "recoveries": 0}
        rows = self.connection.execute(
            "SELECT event_type, created_at FROM task_events ORDER BY event_id"
        ).fetchall()
        for row in rows:
            event_type = str(row["event_type"])
            prefix = event_type.split(".", 1)[0]
            stream = prefix if prefix in aggregates and prefix != "side" else "other"
            if event_type.startswith("side_effect."):
                stream = "side_effect"
            aggregates[stream]["event_count"] += 1
            aggregates[stream]["last_event_at"] = row["created_at"]
            if "failed" in event_type:
                signals["failures"] += 1
            if "cancel" in event_type:
                signals["cancellations"] += 1
            if "recovered" in event_type:
                signals["recoveries"] += 1
        return {
            "status": "available",
            "total_events": len(rows),
            "streams": [aggregates[name] for name in stream_names],
            "signals": signals,
        }

    def system_status_public(self) -> dict:
        integrity = str(self.connection.execute("PRAGMA quick_check(1)").fetchone()[0])
        agent_total, agent_ready = self.connection.execute(
            "SELECT count(*), sum(CASE WHEN status='ready' THEN 1 ELSE 0 END) FROM agents"
        ).fetchone()
        account_total, account_ready = self.connection.execute(
            "SELECT count(*), sum(CASE WHEN status='ready' THEN 1 ELSE 0 END) FROM accounts"
        ).fetchone()
        active_tasks = int(self.connection.execute(
            "SELECT count(*) FROM tasks WHERE status IN ('draft','queued','claimed','running','review')"
        ).fetchone()[0])
        attention_tasks = int(self.connection.execute(
            "SELECT count(*) FROM tasks WHERE status IN ('blocked','failed','rejected')"
        ).fetchone()[0])
        pending_approvals = int(self.connection.execute(
            "SELECT count(*) FROM approvals WHERE decision='pending'"
        ).fetchone()[0])
        pending_side_effects = int(self.connection.execute(
            "SELECT count(*) FROM side_effect_requests WHERE status IN ('pending','approved')"
        ).fetchone()[0])
        agent_ready = int(agent_ready or 0)
        account_ready = int(account_ready or 0)
        storage_healthy = integrity == "ok"
        routing_resilient = account_ready >= 2
        operational = storage_healthy and agent_ready >= 1 and routing_resilient
        return {
            "status": "operational" if operational else "limited",
            "components": [
                {"name": "durable_storage", "status": "ready" if storage_healthy else "degraded", "ready": int(storage_healthy), "total": 1},
                {"name": "agents", "status": "ready" if agent_ready else "unavailable", "ready": agent_ready, "total": int(agent_total)},
                {"name": "accounts", "status": "ready" if account_ready else "unavailable", "ready": account_ready, "total": int(account_total)},
                {"name": "routing_resilience", "status": "ready" if routing_resilient else "degraded", "ready": account_ready, "total": 2},
            ],
            "work": {"active_tasks": active_tasks, "attention_tasks": attention_tasks},
            "gates": {"pending_approvals": pending_approvals, "pending_side_effects": pending_side_effects},
        }

    def create_memory_candidate(self, memory_id: str, scope: str, scope_id: str | None,
                                classification: str, summary: str, source_kind: str,
                                source_reference_id: str, expires_at: str | None = None) -> dict:
        normalized = validate_memory_candidate(summary, source_kind, source_reference_id)
        if scope not in {"task", "project", "mosh"} or classification not in {"internal", "confidential", "restricted"}:
            raise ValueError("invalid memory scope or classification")
        if source_kind == "task":
            exists = self.connection.execute("SELECT 1 FROM tasks WHERE task_id=?", (source_reference_id,)).fetchone()
        else:
            exists = self.connection.execute("SELECT 1 FROM task_events WHERE event_id=?", (source_reference_id,)).fetchone()
        if exists is None:
            raise ValueError("memory source does not exist")
        now = utc_now()
        digest = sha256_text(normalized)
        self.connection.execute(
            """INSERT INTO memory_records(memory_id,scope,scope_id,classification,status,summary,content_sha256,
               source_kind,source_reference_id,created_at,updated_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (memory_id, scope, scope_id, classification, "candidate", normalized, digest,
             source_kind, source_reference_id, now, now, expires_at),
        )
        return self.get_memory_record(memory_id)

    def get_memory_record(self, memory_id: str) -> dict:
        row = self.connection.execute("SELECT * FROM memory_records WHERE memory_id=?", (memory_id,)).fetchone()
        if row is None:
            raise KeyError(memory_id)
        return dict(row)

    def validate_memory(self, memory_id: str, method: str, evidence_ids: list[str]) -> dict:
        record = self.get_memory_record(memory_id)
        if record["status"] != "candidate" or method not in {"owner_review", "test", "cross_source"}:
            raise ValueError("memory is not a valid validation candidate")
        evidence = sorted({item for item in evidence_ids if item})
        if not evidence or len(evidence) > 20:
            raise ValueError("memory validation requires 1..20 evidence IDs")
        now = utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                "INSERT INTO memory_validations(validation_id,memory_id,content_sha256,method,evidence_ids_json,created_at) VALUES(?,?,?,?,?,?)",
                (f"MVAL-{uuid.uuid4().hex.upper()}", memory_id, record["content_sha256"], method, json.dumps(evidence), now),
            )
            self.connection.execute("UPDATE memory_records SET status='validated',updated_at=? WHERE memory_id=?", (now, memory_id))
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get_memory_record(memory_id)

    def request_memory_approval(self, memory_id: str, requested_by: str) -> dict:
        record = self.get_memory_record(memory_id)
        if record["status"] != "validated":
            raise ValueError("only validated memory can request approval")
        approval_id, now = f"MAPR-{uuid.uuid4().hex.upper()}", utc_now()
        self.connection.execute(
            "INSERT INTO memory_approvals(approval_id,memory_id,content_sha256,decision,requested_by,created_at) VALUES(?,?,?,'pending',?,?)",
            (approval_id, memory_id, record["content_sha256"], requested_by, now),
        )
        return dict(self.connection.execute("SELECT * FROM memory_approvals WHERE approval_id=?", (approval_id,)).fetchone())

    def decide_memory_approval(self, approval_id: str, decision: str, decided_by: str, reason: str) -> dict:
        approval = self.connection.execute("SELECT * FROM memory_approvals WHERE approval_id=?", (approval_id,)).fetchone()
        if approval is None or approval["decision"] != "pending" or decision not in {"approved", "rejected"}:
            raise ValueError("memory approval is not pending or decision is invalid")
        record = self.get_memory_record(approval["memory_id"])
        if record["status"] != "validated" or record["content_sha256"] != approval["content_sha256"]:
            raise ValueError("memory content no longer matches approval")
        now = utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                "UPDATE memory_approvals SET decision=?,decided_by=?,reason=?,decided_at=? WHERE approval_id=?",
                (decision, decided_by, reason, now, approval_id),
            )
            self.connection.execute("UPDATE memory_records SET status=?,updated_at=? WHERE memory_id=?", (decision, now, approval["memory_id"]))
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get_memory_record(approval["memory_id"])

    def create_skill_candidate(self, skill_id: str, name: str, scope: str, scope_id: str | None,
                               risk: str, procedure_ref: str, procedure_sha256: str,
                               evidence_ids: list[str]) -> dict:
        procedure_ref = validate_procedure_ref(procedure_ref)
        evidence = sorted({item for item in evidence_ids if item})
        if not name.strip() or len(name) > 120 or scope not in {"agent", "project", "business", "core"}:
            raise ValueError("invalid skill identity or scope")
        if risk not in {"low", "medium", "high", "critical"} or not re.fullmatch(r"[a-f0-9]{64}", procedure_sha256):
            raise ValueError("invalid skill risk or digest")
        if not evidence or len(evidence) > 30:
            raise ValueError("skill requires 1..30 evidence IDs")
        now = utc_now()
        self.connection.execute(
            """INSERT INTO skill_candidates(skill_id,name,scope,scope_id,status,risk,procedure_ref,procedure_sha256,
               evidence_ids_json,test_ids_json,created_at,updated_at) VALUES(?,?,?,?,'candidate',?,?,?,?,'[]',?,?)""",
            (skill_id, name.strip(), scope, scope_id, risk, procedure_ref, procedure_sha256, json.dumps(evidence), now, now),
        )
        return dict(self.connection.execute("SELECT * FROM skill_candidates WHERE skill_id=?", (skill_id,)).fetchone())

    def get_skill_candidate(self, skill_id: str) -> dict:
        row = self.connection.execute("SELECT * FROM skill_candidates WHERE skill_id=?", (skill_id,)).fetchone()
        if row is None:
            raise KeyError(skill_id)
        return dict(row)

    def validate_skill(self, skill_id: str, test_ids: list[str]) -> dict:
        skill = self.get_skill_candidate(skill_id)
        tests = sorted({item for item in test_ids if item})
        if skill["status"] != "candidate" or not tests or len(tests) > 30:
            raise ValueError("skill validation requires a candidate and 1..30 test IDs")
        now = utc_now()
        self.connection.execute(
            "UPDATE skill_candidates SET status='validated',test_ids_json=?,updated_at=? WHERE skill_id=?",
            (json.dumps(tests), now, skill_id),
        )
        return self.get_skill_candidate(skill_id)

    @staticmethod
    def _skill_requires_owner(skill: dict) -> bool:
        return skill["scope"] in {"core", "business"} or skill["risk"] in {"high", "critical"}

    def request_skill_approval(self, skill_id: str, requested_by: str) -> dict:
        skill = self.get_skill_candidate(skill_id)
        if skill["status"] != "validated":
            raise ValueError("only validated skills can request approval")
        if self._skill_requires_owner(skill) and requested_by != "owner":
            raise ValueError("shared or high-risk skills require owner approval")
        approval_id, now = f"SAPR-{uuid.uuid4().hex.upper()}", utc_now()
        self.connection.execute(
            "INSERT INTO skill_approvals(approval_id,skill_id,procedure_sha256,decision,requested_by,created_at) VALUES(?,?,?,'pending',?,?)",
            (approval_id, skill_id, skill["procedure_sha256"], requested_by, now),
        )
        return dict(self.connection.execute("SELECT * FROM skill_approvals WHERE approval_id=?", (approval_id,)).fetchone())

    def decide_skill_approval(self, approval_id: str, decision: str, decided_by: str, reason: str) -> dict:
        approval = self.connection.execute("SELECT * FROM skill_approvals WHERE approval_id=?", (approval_id,)).fetchone()
        if approval is None or approval["decision"] != "pending" or decision not in {"approved", "rejected"}:
            raise ValueError("skill approval is not pending or decision is invalid")
        skill = self.get_skill_candidate(approval["skill_id"])
        if skill["status"] != "validated" or skill["procedure_sha256"] != approval["procedure_sha256"]:
            raise ValueError("skill procedure no longer matches approval")
        if self._skill_requires_owner(skill) and decided_by != "owner":
            raise ValueError("shared or high-risk skills require an owner decision")
        now = utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                "UPDATE skill_approvals SET decision=?,decided_by=?,reason=?,decided_at=? WHERE approval_id=?",
                (decision, decided_by, reason, now, approval_id),
            )
            self.connection.execute("UPDATE skill_candidates SET status=?,updated_at=? WHERE skill_id=?", (decision, now, approval["skill_id"]))
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get_skill_candidate(approval["skill_id"])

    def learning_status_public(self) -> dict:
        memory = {status: 0 for status in ("candidate", "validated", "approved", "rejected", "expired")}
        skills = {status: 0 for status in ("candidate", "validated", "approved", "rejected", "retired")}
        for row in self.connection.execute("SELECT status,count(*) AS total FROM memory_records GROUP BY status"):
            memory[row["status"]] = int(row["total"])
        for row in self.connection.execute("SELECT status,count(*) AS total FROM skill_candidates GROUP BY status"):
            skills[row["status"]] = int(row["total"])
        binding_total = int(self.connection.execute("SELECT count(*) FROM task_memory_bindings").fetchone()[0])
        active_bindings = int(self.connection.execute(
            """SELECT count(*) FROM task_memory_bindings b JOIN memory_records m ON m.memory_id=b.memory_id
               WHERE m.status='approved' AND (m.expires_at IS NULL OR m.expires_at>?)""", (utc_now(),)
        ).fetchone()[0])
        return {
            "memory": memory,
            "skills": skills,
            "pending_approvals": {
                "memory": int(self.connection.execute("SELECT count(*) FROM memory_approvals WHERE decision='pending'").fetchone()[0]),
                "skills": int(self.connection.execute("SELECT count(*) FROM skill_approvals WHERE decision='pending'").fetchone()[0]),
            },
            "embeddings": {"status": "disabled", "indexed_records": 0},
            "task_bindings": {"total": binding_total, "usable": active_bindings},
        }

    def retrieve_approved_memory(self, scope: str, scope_id: str | None, classification: str,
                                 requested_by: str, limit: int = 50) -> list[dict]:
        if scope not in {"task", "project", "mosh"} or classification not in {"internal", "confidential", "restricted"}:
            raise ValueError("invalid retrieval scope or classification")
        if not 1 <= limit <= 100:
            raise ValueError("memory retrieval limit must be 1..100")
        if (scope == "mosh" and scope_id is not None) or (scope != "mosh" and not scope_id):
            raise ValueError("scope ID is required for task/project and prohibited for MOSH scope")
        if classification in {"confidential", "restricted"} and requested_by != "owner":
            raise ValueError("confidential or restricted memory requires owner retrieval")
        rows = self.connection.execute(
            """SELECT memory_id,scope,scope_id,classification,summary,content_sha256,source_kind,source_reference_id,
                      created_at,expires_at
               FROM memory_records
               WHERE status='approved' AND scope=? AND scope_id IS ? AND classification=?
                 AND (expires_at IS NULL OR expires_at>?)
               ORDER BY created_at DESC LIMIT ?""",
            (scope, scope_id, classification, utc_now(), limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def expire_memory(self, memory_id: str, actor: str, reason: str) -> dict:
        record = self.get_memory_record(memory_id)
        if record["status"] != "approved" or not actor.strip() or not reason.strip():
            raise ValueError("only approved memory can be explicitly expired with actor and reason")
        now = utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute("UPDATE memory_records SET status='expired',updated_at=? WHERE memory_id=?", (now, memory_id))
            self.connection.execute(
                "INSERT INTO memory_lifecycle_events(memory_id,action,actor,reason,content_sha256,created_at) VALUES(?,'expired',?,?,?,?)",
                (memory_id, actor.strip(), reason.strip(), record["content_sha256"], now),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get_memory_record(memory_id)

    def retire_skill(self, skill_id: str, actor: str, reason: str) -> dict:
        skill = self.get_skill_candidate(skill_id)
        if skill["status"] != "approved" or not actor.strip() or not reason.strip():
            raise ValueError("only approved skills can be explicitly retired with actor and reason")
        if self._skill_requires_owner(skill) and actor != "owner":
            raise ValueError("shared or high-risk skills require owner retirement")
        now = utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute("UPDATE skill_candidates SET status='retired',updated_at=? WHERE skill_id=?", (now, skill_id))
            self.connection.execute(
                "INSERT INTO skill_lifecycle_events(skill_id,action,actor,reason,procedure_sha256,created_at) VALUES(?,'retired',?,?,?,?)",
                (skill_id, actor.strip(), reason.strip(), skill["procedure_sha256"], now),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get_skill_candidate(skill_id)

    def bind_memory_to_task(self, task_id: str, memory_id: str, bound_by: str, reason: str) -> dict:
        task = self.get_task(task_id)
        if task is None:
            raise ValueError("binding task does not exist")
        memory = self.get_memory_record(memory_id)
        if memory["status"] != "approved" or (memory["expires_at"] and memory["expires_at"] <= utc_now()):
            raise ValueError("only approved, unexpired memory can be bound")
        if memory["classification"] in {"confidential", "restricted"} and bound_by != "owner":
            raise ValueError("confidential or restricted memory requires owner binding")
        if memory["scope"] == "task" and memory["scope_id"] != task_id:
            raise ValueError("task-scoped memory cannot bind to another task")
        if memory["scope"] == "project" and memory["scope_id"] != task.project_id:
            raise ValueError("project-scoped memory cannot bind outside its project")
        if not bound_by.strip() or not reason.strip():
            raise ValueError("binding requires actor and reason")
        if self.connection.execute(
            "SELECT 1 FROM task_memory_bindings WHERE task_id=? AND memory_id=?", (task_id, memory_id)
        ).fetchone():
            raise ValueError("memory is already bound to this task")
        binding_id, now = f"MBIND-{uuid.uuid4().hex.upper()}", utc_now()
        self.connection.execute(
            "INSERT INTO task_memory_bindings(binding_id,task_id,memory_id,content_sha256,bound_by,reason,created_at) VALUES(?,?,?,?,?,?,?)",
            (binding_id, task_id, memory_id, memory["content_sha256"], bound_by.strip(), reason.strip(), now),
        )
        return dict(self.connection.execute("SELECT * FROM task_memory_bindings WHERE binding_id=?", (binding_id,)).fetchone())

    def list_task_memory_bindings(self, task_id: str) -> list[dict]:
        if self.get_task(task_id) is None:
            raise ValueError("binding task does not exist")
        now = utc_now()
        rows = self.connection.execute(
            """SELECT b.binding_id,b.task_id,b.memory_id,b.content_sha256,b.bound_by,b.reason,b.created_at,
                      m.scope,m.classification,m.status AS memory_status,
                      CASE WHEN m.status='approved' AND (m.expires_at IS NULL OR m.expires_at>?) THEN 1 ELSE 0 END AS usable
               FROM task_memory_bindings b JOIN memory_records m ON m.memory_id=b.memory_id
               WHERE b.task_id=? ORDER BY b.created_at""",
            (now, task_id),
        ).fetchall()
        return [dict(row) for row in rows]

    def _owned_run(self, run_id: str, worker_id: str, expected: RunStatus) -> TaskRun:
        run = self.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status != expected or run.lease_owner != worker_id:
            raise ValueError("worker does not own run in expected state")
        return run

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> TaskRun:
        return TaskRun(
            run_id=row["run_id"], task_id=row["task_id"], attempt=row["attempt"],
            account_id=row["account_id"], status=RunStatus(row["status"]), lease_owner=row["lease_owner"],
            lease_expires_at=row["lease_expires_at"], result=row["result"], error=row["error"],
            completion_key=row["completion_key"],
            adapter_name=row["adapter_name"], command_argv_json=row["command_argv_json"],
            command_env_keys_json=row["command_env_keys_json"], command_sha256=row["command_sha256"],
            command_recorded_at=row["command_recorded_at"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _approval_from_row(row: sqlite3.Row) -> Approval:
        return Approval(
            approval_id=row["approval_id"], task_id=row["task_id"], scope=row["scope"],
            requested_by=row["requested_by"], decision=ApprovalDecision(row["decision"]),
            decided_by=row["decided_by"], reason=row["reason"], created_at=row["created_at"],
            decided_at=row["decided_at"],
        )

    @staticmethod
    def _side_effect_from_row(row: sqlite3.Row) -> SideEffectRequest:
        return SideEffectRequest(
            request_id=row["request_id"], task_id=row["task_id"], action=row["action"],
            target=row["target"], parameters=json.loads(row["parameters_json"]), risk=Risk(row["risk"]),
            request_digest=row["request_digest"], status=SideEffectStatus(row["status"]),
            requested_by=row["requested_by"], decided_by=row["decided_by"], reason=row["reason"],
            expires_at=row["expires_at"], created_at=row["created_at"], decided_at=row["decided_at"],
            consumed_at=row["consumed_at"],
        )

    @staticmethod
    def _side_effect_execution_from_row(row: sqlite3.Row) -> SideEffectExecution:
        return SideEffectExecution(
            execution_id=row["execution_id"], request_id=row["request_id"],
            idempotency_key=row["idempotency_key"], status=SideEffectExecutionStatus(row["status"]),
            artifact_path=row["artifact_path"], artifact_sha256=row["artifact_sha256"],
            error=row["error"], prepared_at=row["prepared_at"], completed_at=row["completed_at"],
            failed_at=row["failed_at"],
        )

    @staticmethod
    def _cleanup_plan_from_row(row: sqlite3.Row) -> CleanupPlan:
        return CleanupPlan(
            plan_id=row["plan_id"], task_id=row["task_id"], execution_id=row["execution_id"],
            source_path=row["source_path"], trash_path=row["trash_path"],
            artifact_sha256=row["artifact_sha256"], plan_digest=row["plan_digest"],
            status=CleanupStatus(row["status"]), created_at=row["created_at"],
            trashed_at=row["trashed_at"], restored_at=row["restored_at"], retain_until=row["retain_until"],
        )

    @staticmethod
    def _cleanup_operation_from_row(row: sqlite3.Row) -> CleanupOperation:
        return CleanupOperation(
            operation_id=row["operation_id"], plan_id=row["plan_id"], request_id=row["request_id"],
            action=row["action"], status=CleanupOperationStatus(row["status"]), error=row["error"],
            prepared_at=row["prepared_at"], completed_at=row["completed_at"], failed_at=row["failed_at"],
        )

    def _event(self, task_id: str, event_type: str, source: TaskStatus | None,
               target: TaskStatus | None, payload: dict) -> None:
        self.connection.execute(
            "INSERT INTO task_events(task_id,event_type,from_status,to_status,payload_json,created_at) VALUES(?,?,?,?,?,?)",
            (task_id, event_type, source.value if source else None, target.value if target else None,
             json.dumps(payload, separators=(",", ":")), utc_now()),
        )
