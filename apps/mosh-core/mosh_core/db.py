from __future__ import annotations

import sqlite3
from pathlib import Path


MIGRATIONS = (
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS accounts (
        account_id TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        alias TEXT NOT NULL,
        credential_ref TEXT NOT NULL,
        status TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        last_verified_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(provider, alias)
    );
    CREATE TABLE IF NOT EXISTS agents (
        agent_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        adapter TEXT NOT NULL,
        status TEXT NOT NULL,
        capabilities_json TEXT NOT NULL DEFAULT '[]',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS tasks (
        task_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        from_actor TEXT NOT NULL,
        to_agent TEXT NOT NULL,
        provider TEXT,
        account_id TEXT REFERENCES accounts(account_id),
        objective TEXT NOT NULL,
        inputs_json TEXT NOT NULL,
        acceptance_json TEXT NOT NULL,
        constraints_json TEXT NOT NULL,
        risk TEXT NOT NULL,
        approval_required INTEGER NOT NULL CHECK (approval_required IN (0, 1)),
        status TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK ((provider IS NULL AND account_id IS NULL) OR (provider IS NOT NULL AND account_id IS NOT NULL))
    );
    CREATE TABLE IF NOT EXISTS task_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL REFERENCES tasks(task_id),
        event_type TEXT NOT NULL,
        from_status TEXT,
        to_status TEXT,
        payload_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
    CREATE INDEX IF NOT EXISTS idx_events_task ON task_events(task_id, event_id);
    """,
    """
    CREATE TABLE IF NOT EXISTS task_runs (
        run_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES tasks(task_id),
        attempt INTEGER NOT NULL CHECK (attempt >= 1),
        account_id TEXT NOT NULL REFERENCES accounts(account_id),
        status TEXT NOT NULL,
        lease_owner TEXT,
        lease_expires_at TEXT,
        result TEXT,
        error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(task_id, attempt)
    );
    CREATE INDEX IF NOT EXISTS idx_runs_claim ON task_runs(status, created_at);
    CREATE INDEX IF NOT EXISTS idx_runs_lease ON task_runs(status, lease_expires_at);
    """,
    """
    ALTER TABLE tasks ADD COLUMN cancellation_requested INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE tasks ADD COLUMN cancellation_reason TEXT;
    ALTER TABLE task_runs ADD COLUMN completion_key TEXT;
    CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_completion_key ON task_runs(completion_key) WHERE completion_key IS NOT NULL;
    """,
    """
    CREATE TABLE IF NOT EXISTS approvals (
        approval_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES tasks(task_id),
        scope TEXT NOT NULL,
        requested_by TEXT NOT NULL,
        decision TEXT NOT NULL CHECK (decision IN ('pending','approved','rejected')),
        decided_by TEXT,
        reason TEXT,
        created_at TEXT NOT NULL,
        decided_at TEXT,
        UNIQUE(task_id, scope)
    );
    CREATE INDEX IF NOT EXISTS idx_approvals_task ON approvals(task_id, scope, decision);
    """,
    """
    CREATE TABLE IF NOT EXISTS side_effect_requests (
        request_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES tasks(task_id),
        action TEXT NOT NULL,
        target TEXT NOT NULL,
        parameters_json TEXT NOT NULL,
        risk TEXT NOT NULL,
        request_digest TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending','approved','rejected','consumed')),
        requested_by TEXT NOT NULL,
        decided_by TEXT,
        reason TEXT,
        expires_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        decided_at TEXT,
        consumed_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_side_effect_task ON side_effect_requests(task_id, status);
    """,
    """
    CREATE TABLE IF NOT EXISTS side_effect_executions (
        execution_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL UNIQUE REFERENCES side_effect_requests(request_id),
        idempotency_key TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL CHECK (status IN ('prepared','completed','failed')),
        artifact_path TEXT NOT NULL,
        artifact_sha256 TEXT NOT NULL,
        error TEXT,
        prepared_at TEXT NOT NULL,
        completed_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_side_effect_execution_status ON side_effect_executions(status);
    """,
    """
    ALTER TABLE side_effect_executions ADD COLUMN failed_at TEXT;
    """,
    """
    CREATE TABLE IF NOT EXISTS cleanup_plans (
        plan_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES tasks(task_id),
        execution_id TEXT NOT NULL REFERENCES side_effect_executions(execution_id),
        source_path TEXT NOT NULL,
        trash_path TEXT NOT NULL,
        artifact_sha256 TEXT NOT NULL,
        plan_digest TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('planned','trashed','restored')),
        created_at TEXT NOT NULL,
        trashed_at TEXT,
        restored_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_cleanup_status ON cleanup_plans(status, created_at);
    """,
    """
    ALTER TABLE cleanup_plans ADD COLUMN retain_until TEXT;
    CREATE TABLE IF NOT EXISTS cleanup_operations (
        operation_id TEXT PRIMARY KEY,
        plan_id TEXT NOT NULL REFERENCES cleanup_plans(plan_id),
        request_id TEXT NOT NULL UNIQUE REFERENCES side_effect_requests(request_id),
        action TEXT NOT NULL CHECK (action IN ('trash','restore')),
        status TEXT NOT NULL CHECK (status IN ('prepared','completed','failed')),
        error TEXT,
        prepared_at TEXT NOT NULL,
        completed_at TEXT,
        failed_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_cleanup_operation_status ON cleanup_operations(status, prepared_at);
    """,
    """
    ALTER TABLE task_runs ADD COLUMN adapter_name TEXT;
    ALTER TABLE task_runs ADD COLUMN command_argv_json TEXT;
    ALTER TABLE task_runs ADD COLUMN command_env_keys_json TEXT;
    ALTER TABLE task_runs ADD COLUMN command_sha256 TEXT;
    ALTER TABLE task_runs ADD COLUMN command_recorded_at TEXT;
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_records (
        memory_id TEXT PRIMARY KEY,
        scope TEXT NOT NULL CHECK (scope IN ('task','project','mosh')),
        scope_id TEXT,
        classification TEXT NOT NULL CHECK (classification IN ('internal','confidential','restricted')),
        status TEXT NOT NULL CHECK (status IN ('candidate','validated','approved','rejected','expired')),
        summary TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        source_kind TEXT NOT NULL CHECK (source_kind IN ('task','event')),
        source_reference_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        expires_at TEXT
    );
    CREATE TABLE IF NOT EXISTS memory_validations (
        validation_id TEXT PRIMARY KEY,
        memory_id TEXT NOT NULL REFERENCES memory_records(memory_id),
        content_sha256 TEXT NOT NULL,
        method TEXT NOT NULL CHECK (method IN ('owner_review','test','cross_source')),
        evidence_ids_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS memory_approvals (
        approval_id TEXT PRIMARY KEY,
        memory_id TEXT NOT NULL REFERENCES memory_records(memory_id),
        content_sha256 TEXT NOT NULL,
        decision TEXT NOT NULL CHECK (decision IN ('pending','approved','rejected')),
        requested_by TEXT NOT NULL,
        decided_by TEXT,
        reason TEXT,
        created_at TEXT NOT NULL,
        decided_at TEXT,
        UNIQUE(memory_id, content_sha256)
    );
    CREATE TABLE IF NOT EXISTS skill_candidates (
        skill_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        scope TEXT NOT NULL CHECK (scope IN ('agent','project','business','core')),
        scope_id TEXT,
        status TEXT NOT NULL CHECK (status IN ('candidate','validated','approved','rejected','retired')),
        risk TEXT NOT NULL CHECK (risk IN ('low','medium','high','critical')),
        procedure_ref TEXT NOT NULL,
        procedure_sha256 TEXT NOT NULL,
        evidence_ids_json TEXT NOT NULL,
        test_ids_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS skill_approvals (
        approval_id TEXT PRIMARY KEY,
        skill_id TEXT NOT NULL REFERENCES skill_candidates(skill_id),
        procedure_sha256 TEXT NOT NULL,
        decision TEXT NOT NULL CHECK (decision IN ('pending','approved','rejected')),
        requested_by TEXT NOT NULL,
        decided_by TEXT,
        reason TEXT,
        created_at TEXT NOT NULL,
        decided_at TEXT,
        UNIQUE(skill_id, procedure_sha256)
    );
    CREATE INDEX IF NOT EXISTS idx_memory_status ON memory_records(status, scope);
    CREATE INDEX IF NOT EXISTS idx_skill_status ON skill_candidates(status, scope);
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_lifecycle_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        memory_id TEXT NOT NULL REFERENCES memory_records(memory_id),
        action TEXT NOT NULL CHECK (action IN ('expired')),
        actor TEXT NOT NULL,
        reason TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS skill_lifecycle_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        skill_id TEXT NOT NULL REFERENCES skill_candidates(skill_id),
        action TEXT NOT NULL CHECK (action IN ('retired')),
        actor TEXT NOT NULL,
        reason TEXT NOT NULL,
        procedure_sha256 TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_memory_lifecycle ON memory_lifecycle_events(memory_id, event_id);
    CREATE INDEX IF NOT EXISTS idx_skill_lifecycle ON skill_lifecycle_events(skill_id, event_id);
    """,
    """
    CREATE TABLE IF NOT EXISTS task_memory_bindings (
        binding_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES tasks(task_id),
        memory_id TEXT NOT NULL REFERENCES memory_records(memory_id),
        content_sha256 TEXT NOT NULL,
        bound_by TEXT NOT NULL,
        reason TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(task_id, memory_id)
    );
    CREATE INDEX IF NOT EXISTS idx_task_memory_task ON task_memory_bindings(task_id, created_at);
    """,
    """
    CREATE TABLE IF NOT EXISTS plugin_registrations (
        plugin_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        provider TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('unconfigured','disconnected','connected','degraded','disabled')),
        auth_type TEXT NOT NULL CHECK (auth_type IN ('oauth','github_app','token','none')),
        health_status TEXT NOT NULL CHECK (health_status IN ('unknown','healthy','degraded','unavailable')),
        capabilities_json TEXT NOT NULL DEFAULT '[]',
        requested_scopes_json TEXT NOT NULL DEFAULT '[]',
        granted_scopes_json TEXT NOT NULL DEFAULT '[]',
        last_checked_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS plugin_agent_access (
        plugin_id TEXT NOT NULL REFERENCES plugin_registrations(plugin_id),
        agent_id TEXT NOT NULL REFERENCES agents(agent_id),
        allowed_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(plugin_id, agent_id)
    );
    CREATE INDEX IF NOT EXISTS idx_plugin_status ON plugin_registrations(status, health_status);
    """,
    """
    CREATE TABLE IF NOT EXISTS plugin_repository_access (
        plugin_id TEXT NOT NULL REFERENCES plugin_registrations(plugin_id),
        repository_full_name TEXT NOT NULL,
        allowed_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(plugin_id, repository_full_name)
    );
    CREATE TABLE IF NOT EXISTS plugin_read_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        plugin_id TEXT NOT NULL REFERENCES plugin_registrations(plugin_id),
        agent_id TEXT NOT NULL REFERENCES agents(agent_id),
        operation TEXT NOT NULL CHECK (operation IN ('repository.metadata','file.read')),
        repository_sha256 TEXT NOT NULL,
        resource_sha256 TEXT,
        start_line INTEGER,
        end_line INTEGER,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_plugin_read_events ON plugin_read_events(plugin_id, agent_id, event_id);
    """,
    """
    CREATE TABLE IF NOT EXISTS plugin_read_outcomes (
        event_id INTEGER PRIMARY KEY REFERENCES plugin_read_events(event_id),
        status TEXT NOT NULL CHECK (status IN ('completed','failed')),
        response_sha256 TEXT,
        returned_bytes INTEGER CHECK (returned_bytes IS NULL OR returned_bytes >= 0),
        returned_lines INTEGER CHECK (returned_lines IS NULL OR returned_lines >= 0),
        error_code TEXT,
        completed_at TEXT NOT NULL,
        CHECK (
            (status='completed' AND response_sha256 IS NOT NULL AND error_code IS NULL) OR
            (status='failed' AND response_sha256 IS NULL AND returned_bytes IS NULL AND returned_lines IS NULL AND error_code IS NOT NULL)
        )
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS project_governance (
        project_id TEXT PRIMARY KEY,
        profile TEXT NOT NULL CHECK (profile IN ('experiment','business')),
        classification TEXT NOT NULL CHECK (classification IN ('internal','confidential','restricted')),
        isolation_key TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL CHECK (status IN ('draft','active','blocked')),
        compliance_claims_prohibited INTEGER NOT NULL DEFAULT 1 CHECK (compliance_claims_prohibited=1),
        approved_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS project_governance_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL REFERENCES project_governance(project_id),
        action TEXT NOT NULL CHECK (action IN ('configured','activated','blocked')),
        actor TEXT NOT NULL,
        profile TEXT NOT NULL,
        classification TEXT NOT NULL,
        isolation_sha256 TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_project_governance_status ON project_governance(status, profile, classification);
    """,
    """
    CREATE TABLE IF NOT EXISTS governance_requirements (
        requirement_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES project_governance(project_id),
        requirement_key TEXT NOT NULL,
        required INTEGER NOT NULL CHECK (required IN (0,1)),
        status TEXT NOT NULL CHECK (status IN ('gap','evidenced','waived')),
        evidence_sha256 TEXT,
        decided_by TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(project_id, requirement_key),
        CHECK ((status='evidenced' AND evidence_sha256 IS NOT NULL AND decided_by IS NOT NULL) OR
               (status='waived' AND required=0 AND evidence_sha256 IS NULL AND decided_by IS NOT NULL) OR
               (status='gap' AND evidence_sha256 IS NULL AND decided_by IS NULL))
    );
    CREATE INDEX IF NOT EXISTS idx_governance_requirements ON governance_requirements(project_id, status, required);
    """,
    """
    CREATE TABLE IF NOT EXISTS change_requests (
        change_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES project_governance(project_id),
        change_type TEXT NOT NULL CHECK (change_type IN ('configuration','code','deployment','plugin','data')),
        risk TEXT NOT NULL CHECK (risk IN ('low','medium','high','critical')),
        description_sha256 TEXT NOT NULL,
        evidence_sha256 TEXT NOT NULL,
        request_sha256 TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL CHECK (status IN ('pending','approved','rejected')),
        requested_by TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS change_decisions (
        change_id TEXT PRIMARY KEY REFERENCES change_requests(change_id),
        decision TEXT NOT NULL CHECK (decision IN ('approved','rejected')),
        decided_by TEXT NOT NULL,
        reason_sha256 TEXT NOT NULL,
        request_sha256 TEXT NOT NULL,
        decided_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_change_requests ON change_requests(project_id, status, risk);
    """,
    """
    CREATE TABLE IF NOT EXISTS governance_artifacts (
        artifact_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES project_governance(project_id),
        artifact_type TEXT NOT NULL CHECK (artifact_type IN ('threat_model','security_review','privacy_review','vendor_assessment','incident_runbook','handover')),
        artifact_sha256 TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending','accepted','rejected')),
        submitted_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(project_id, artifact_type, artifact_sha256)
    );
    CREATE TABLE IF NOT EXISTS governance_artifact_decisions (
        artifact_id TEXT PRIMARY KEY REFERENCES governance_artifacts(artifact_id),
        decision TEXT NOT NULL CHECK (decision IN ('accepted','rejected')),
        decided_by TEXT NOT NULL,
        reason_sha256 TEXT NOT NULL,
        decided_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_governance_artifacts ON governance_artifacts(project_id, artifact_type, status);
    """,
    """
    CREATE TABLE IF NOT EXISTS workflow_patterns (
        pattern_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        source_type TEXT NOT NULL CHECK (source_type IN ('local','skill','plugin','mcp','n8n')),
        definition_sha256 TEXT NOT NULL,
        risk TEXT NOT NULL CHECK (risk IN ('low','medium','high','critical')),
        requires_external_effects INTEGER NOT NULL CHECK (requires_external_effects IN (0,1)),
        status TEXT NOT NULL CHECK (status IN ('candidate','approved','rejected','retired')),
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(source_type, definition_sha256)
    );
    CREATE TABLE IF NOT EXISTS workflow_pattern_decisions (
        pattern_id TEXT PRIMARY KEY REFERENCES workflow_patterns(pattern_id),
        decision TEXT NOT NULL CHECK (decision IN ('approved','rejected')),
        decided_by TEXT NOT NULL,
        evidence_sha256 TEXT NOT NULL,
        decided_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_workflow_patterns ON workflow_patterns(status, source_type, risk);
    """,
    """
    CREATE TABLE IF NOT EXISTS workflow_plans (
        plan_id TEXT PRIMARY KEY,
        pattern_id TEXT NOT NULL REFERENCES workflow_patterns(pattern_id),
        input_sha256 TEXT NOT NULL,
        plan_sha256 TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL CHECK (status IN ('ready','approval_required')),
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_workflow_plans ON workflow_plans(status, pattern_id);
    """,
    """
    CREATE TABLE IF NOT EXISTS automation_tools (
        tool_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        protocol TEXT NOT NULL CHECK (protocol IN ('local','mcp','http')),
        capability TEXT NOT NULL,
        effect_class TEXT NOT NULL CHECK (effect_class IN ('none','read','write','destructive')),
        descriptor_sha256 TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('observed','approved','rejected')),
        observed_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(protocol, descriptor_sha256)
    );
    CREATE TABLE IF NOT EXISTS automation_tool_decisions (
        tool_id TEXT PRIMARY KEY REFERENCES automation_tools(tool_id),
        decision TEXT NOT NULL CHECK (decision IN ('approved','rejected')),
        decided_by TEXT NOT NULL,
        evidence_sha256 TEXT NOT NULL,
        decided_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_automation_tools ON automation_tools(status, protocol, effect_class);
    """,
    """
    CREATE TABLE IF NOT EXISTS video_projects (
        video_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES project_governance(project_id),
        brief_sha256 TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('draft','in_production','review','approved','cancelled')),
        created_by TEXT NOT NULL,
        approved_by TEXT,
        review_sha256 TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK ((status='approved' AND approved_by IS NOT NULL AND review_sha256 IS NOT NULL) OR status!='approved')
    );
    CREATE TABLE IF NOT EXISTS video_stage_records (
        video_id TEXT NOT NULL REFERENCES video_projects(video_id),
        stage TEXT NOT NULL CHECK (stage IN ('inspiration','story','scene_plan','media','narration','composition','render','review','publish')),
        sequence_no INTEGER NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending','completed')),
        evidence_sha256 TEXT,
        completed_by TEXT,
        completed_at TEXT,
        PRIMARY KEY(video_id, stage),
        UNIQUE(video_id, sequence_no),
        CHECK ((status='completed' AND evidence_sha256 IS NOT NULL AND completed_by IS NOT NULL AND completed_at IS NOT NULL) OR
               (status='pending' AND evidence_sha256 IS NULL AND completed_by IS NULL AND completed_at IS NULL))
    );
    CREATE INDEX IF NOT EXISTS idx_video_projects ON video_projects(status, project_id);
    CREATE INDEX IF NOT EXISTS idx_video_stages ON video_stage_records(video_id, sequence_no, status);
    """,
    """
    CREATE TABLE IF NOT EXISTS identity_providers (
        provider_id TEXT PRIMARY KEY,
        provider_type TEXT NOT NULL CHECK (provider_type IN ('local_dev','google_oidc','oidc')),
        issuer TEXT NOT NULL,
        client_id_sha256 TEXT,
        development_only INTEGER NOT NULL CHECK (development_only IN (0,1)),
        status TEXT NOT NULL CHECK (status IN ('configured','disabled')),
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        CHECK ((provider_type='local_dev' AND development_only=1 AND client_id_sha256 IS NULL) OR
               (provider_type!='local_dev' AND development_only=0 AND client_id_sha256 IS NOT NULL))
    );
    CREATE TABLE IF NOT EXISTS identity_principals (
        principal_id TEXT PRIMARY KEY,
        provider_id TEXT NOT NULL REFERENCES identity_providers(provider_id),
        subject_sha256 TEXT NOT NULL,
        display_alias TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('active','disabled')),
        created_at TEXT NOT NULL,
        UNIQUE(provider_id, subject_sha256)
    );
    CREATE TABLE IF NOT EXISTS workspace_role_bindings (
        principal_id TEXT NOT NULL REFERENCES identity_principals(principal_id),
        role TEXT NOT NULL CHECK (role IN ('owner','admin','developer','operator','client','viewer')),
        granted_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(principal_id, role)
    );
    CREATE INDEX IF NOT EXISTS idx_identity_providers ON identity_providers(provider_type, status);
    CREATE INDEX IF NOT EXISTS idx_identity_principals ON identity_principals(provider_id, status);
    """,
    """
    CREATE TABLE IF NOT EXISTS identity_sessions (
        session_id TEXT PRIMARY KEY,
        principal_id TEXT NOT NULL REFERENCES identity_principals(principal_id),
        token_sha256 TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        revoked_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_identity_sessions_active
        ON identity_sessions(token_sha256, expires_at, revoked_at);
    """,
    """
    CREATE TABLE IF NOT EXISTS plugin_repository_access_grants (
        grant_id INTEGER PRIMARY KEY AUTOINCREMENT,
        plugin_id TEXT NOT NULL REFERENCES plugin_registrations(plugin_id),
        repository_full_name TEXT NOT NULL,
        allowed_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT,
        revoked_at TEXT,
        revoked_by TEXT,
        revocation_reason TEXT,
        CHECK ((revoked_at IS NULL AND revoked_by IS NULL AND revocation_reason IS NULL) OR
               (revoked_at IS NOT NULL AND revoked_by IS NOT NULL AND revocation_reason IS NOT NULL))
    );
    CREATE INDEX IF NOT EXISTS idx_plugin_repository_grants_active
        ON plugin_repository_access_grants(plugin_id, repository_full_name, revoked_at, expires_at);
    """,
    """
    INSERT INTO plugin_repository_access_grants(
        plugin_id,repository_full_name,allowed_by,created_at,expires_at,revoked_at,revoked_by,revocation_reason
    )
    SELECT plugin_id,repository_full_name,allowed_by,created_at,NULL,NULL,NULL,NULL
    FROM plugin_repository_access;
    """,
    """
    CREATE TABLE IF NOT EXISTS n8n_discovery_requests (
        request_id TEXT PRIMARY KEY,
        request_sha256 TEXT NOT NULL UNIQUE CHECK (length(request_sha256)=64),
        purpose_sha256 TEXT NOT NULL CHECK (length(purpose_sha256)=64),
        max_workflows INTEGER NOT NULL CHECK (max_workflows BETWEEN 1 AND 25),
        max_response_bytes INTEGER NOT NULL CHECK (max_response_bytes BETWEEN 1 AND 1048576),
        requested_by TEXT NOT NULL,
        approved_by TEXT NOT NULL CHECK (approved_by='owner'),
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS n8n_discovery_outcomes (
        request_id TEXT PRIMARY KEY REFERENCES n8n_discovery_requests(request_id),
        status TEXT NOT NULL CHECK (status IN ('completed','failed')),
        workflow_count INTEGER,
        active_count INTEGER,
        inactive_count INTEGER,
        workflow_identity_set_sha256 TEXT,
        response_sha256 TEXT,
        error_code TEXT,
        completed_at TEXT NOT NULL,
        CHECK (
            (status='completed' AND workflow_count IS NOT NULL AND active_count IS NOT NULL
             AND inactive_count IS NOT NULL AND workflow_identity_set_sha256 IS NOT NULL
             AND response_sha256 IS NOT NULL AND error_code IS NULL)
            OR
            (status='failed' AND workflow_count IS NULL AND active_count IS NULL
             AND inactive_count IS NULL AND workflow_identity_set_sha256 IS NULL
             AND response_sha256 IS NULL AND error_code IS NOT NULL)
        )
    );
    """,
)


def connect(path: str | Path) -> sqlite3.Connection:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target, timeout=10, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    if journal_mode != "wal":
        connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")
    return connection


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
    for version, script in enumerate(MIGRATIONS, start=1):
        if version in applied:
            continue
        connection.executescript("BEGIN IMMEDIATE;\n" + script)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
            (version,),
        )
        connection.commit()
