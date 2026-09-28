from __future__ import annotations

import argparse
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .adapters import ClineAdapter, CodexAdapter, HermesAdapter
from .models import Account, AccountStatus, Agent, AgentStatus, Risk
from .repository import MoshRepository
from .models import TaskEnvelope
from .local_effects import LocalMarkerExecutor
from .cleanup import MarkerCleanup
from .worker import Worker
from .api import TokenFile, serve
from .backup import create_backup, verify_backup
from .n8n_discovery_runner import authorize_packet_file, execute_approved_discovery
from .n8n_discovery import GovernedN8nDiscovery


def default_db() -> Path:
    return Path.cwd() / "data" / "mosh.db"


def main() -> int:
    parser = argparse.ArgumentParser(prog="mosh-core")
    parser.add_argument("--db", type=Path, default=default_db())
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    health = sub.add_parser("health")
    health.add_argument("--accounts-root", type=Path, default=Path.cwd() / ".local" / "codex-accounts")
    bootstrap = sub.add_parser("bootstrap")
    bootstrap.add_argument("--accounts-root", type=Path, default=Path.cwd() / ".local" / "codex-accounts")
    sub.add_parser("summary")
    backup = sub.add_parser("backup-db")
    backup.add_argument("--output-dir", type=Path, required=True)
    verify = sub.add_parser("verify-backup")
    verify.add_argument("--backup", type=Path, required=True)
    n8n_discover = sub.add_parser("n8n-discover")
    n8n_discover.add_argument("--request-id", required=True)
    n8n_authorize = sub.add_parser("n8n-authorize-discovery")
    n8n_authorize.add_argument("--packet", type=Path, required=True)
    n8n_authorize.add_argument("--requested-by", required=True)
    n8n_authorize.add_argument("--approved-by", required=True)
    n8n_status = sub.add_parser("n8n-discovery-status")
    n8n_status.add_argument("--request-id")
    api_server = sub.add_parser("serve-readonly")
    api_server.add_argument("--port", type=int, default=8765)
    api_server.add_argument("--token-file", type=Path, default=Path.cwd() / ".local" / "api" / "token")
    local_server = sub.add_parser("serve-local")
    local_server.add_argument("--port", type=int, default=8765)
    local_server.add_argument("--token-file", type=Path, default=Path.cwd() / ".local" / "api" / "token")
    local_server.add_argument("--write-token-file", type=Path, default=Path.cwd() / ".local" / "api" / "write-token")
    token_init = sub.add_parser("api-token-init")
    token_init.add_argument("--token-file", type=Path, default=Path.cwd() / ".local" / "api" / "token")
    token_init.add_argument("--rotate", action="store_true")
    acceptance = sub.add_parser("adapter-acceptance")
    acceptance.add_argument("--state-root", type=Path, default=Path.cwd() / ".local" / "adapter-acceptance")
    enqueue = sub.add_parser("enqueue")
    enqueue.add_argument("--task-id", required=True)
    enqueue.add_argument("--provider", choices=("codex", "cline", "hermes"), default="codex")
    enqueue.add_argument("--account", required=True, help="Provider account alias")
    enqueue.add_argument("--objective", required=True)
    enqueue.add_argument("--risk", choices=tuple(item.value for item in Risk), default=Risk.LOW.value)
    enqueue.add_argument("--approval-required", action="store_true")
    bind_cline = sub.add_parser("bind-cline-account")
    bind_cline.add_argument("--alias", default="primary")
    bind_cline.add_argument("--state-root", type=Path, default=Path.cwd() / ".local" / "adapter-acceptance-v1")
    bind_hermes = sub.add_parser("bind-hermes-account")
    bind_hermes.add_argument("--alias", default="primary")
    bind_hermes.add_argument("--state-root", type=Path, default=Path.cwd() / ".local" / "adapter-acceptance-v1")
    reroute = sub.add_parser("reroute")
    reroute.add_argument("--task-id", required=True)
    reroute.add_argument("--account", required=True, help="Target Codex account alias")
    reroute.add_argument("--reason", required=True)
    worker = sub.add_parser("worker-once")
    worker.add_argument("--worker-id", default="mosh-worker-1")
    worker.add_argument("--accounts-root", type=Path, default=Path.cwd() / ".local" / "codex-accounts")
    worker.add_argument("--cline-state-root", type=Path, default=Path.cwd() / ".local" / "adapter-acceptance-v1")
    task_status = sub.add_parser("task")
    task_status.add_argument("--task-id", required=True)
    cancel = sub.add_parser("cancel")
    cancel.add_argument("--task-id", required=True)
    cancel.add_argument("--reason", required=True)
    approve = sub.add_parser("approve")
    approve.add_argument("--task-id", required=True)
    approve.add_argument("--by", default="owner")
    approve.add_argument("--reason", required=True)
    reject = sub.add_parser("reject")
    reject.add_argument("--task-id", required=True)
    reject.add_argument("--by", default="owner")
    reject.add_argument("--reason", required=True)
    request_effect = sub.add_parser("request-side-effect")
    request_effect.add_argument("--task-id", required=True)
    request_effect.add_argument("--action", required=True)
    request_effect.add_argument("--target", required=True)
    request_effect.add_argument("--parameters", default="{}", help="JSON object")
    request_effect.add_argument("--risk", choices=tuple(item.value for item in Risk), required=True)
    request_effect.add_argument("--by", default="agent")
    request_effect.add_argument("--expires-minutes", type=int, default=15)
    for name in ("approve-side-effect", "reject-side-effect"):
        decision = sub.add_parser(name)
        decision.add_argument("--request-id", required=True)
        decision.add_argument("--digest", required=True)
        decision.add_argument("--by", default="owner")
        decision.add_argument("--reason", required=True)
    consume_effect = sub.add_parser("consume-side-effect")
    consume_effect.add_argument("--request-id", required=True)
    consume_effect.add_argument("--action", required=True)
    consume_effect.add_argument("--target", required=True)
    consume_effect.add_argument("--parameters", default="{}", help="JSON object")
    execute_marker = sub.add_parser("execute-local-marker")
    execute_marker.add_argument("--request-id", required=True)
    execute_marker.add_argument("--root", type=Path, default=Path.cwd() / "data" / "side-effects")
    recover_marker = sub.add_parser("recover-local-marker")
    recover_marker.add_argument("--execution-id", required=True)
    recover_marker.add_argument("--root", type=Path, default=Path.cwd() / "data" / "side-effects")
    reconcile_markers = sub.add_parser("reconcile-local-markers")
    reconcile_markers.add_argument("--root", type=Path, default=Path.cwd() / "data" / "side-effects")
    sub.add_parser("list-side-effect-executions")
    plan_cleanup = sub.add_parser("plan-marker-cleanup")
    plan_cleanup.add_argument("--task-id", required=True)
    plan_cleanup.add_argument("--execution-id", required=True)
    plan_cleanup.add_argument("--root", type=Path, default=Path.cwd() / "data" / "side-effects")
    plan_cleanup.add_argument("--trash-root", type=Path, default=Path.cwd() / "data" / "side-effects-trash")
    plan_cleanup.add_argument("--expires-minutes", type=int, default=15)
    plan_cleanup.add_argument("--by", default="agent")
    plan_cleanup.add_argument("--retention-days", type=int, default=30)
    for name in ("execute-marker-cleanup", "request-marker-restore", "execute-marker-restore"):
        command = sub.add_parser(name)
        command.add_argument("--plan-id", required=True)
        command.add_argument("--root", type=Path, default=Path.cwd() / "data" / "side-effects")
        command.add_argument("--trash-root", type=Path, default=Path.cwd() / "data" / "side-effects-trash")
        if name == "request-marker-restore":
            command.add_argument("--expires-minutes", type=int, default=15)
            command.add_argument("--by", default="agent")
    inspect_cleanup = sub.add_parser("list-cleanup-plans")
    reconcile_cleanup = sub.add_parser("reconcile-marker-cleanup")
    reconcile_cleanup.add_argument("--root", type=Path, default=Path.cwd() / "data" / "side-effects")
    reconcile_cleanup.add_argument("--trash-root", type=Path, default=Path.cwd() / "data" / "side-effects-trash")
    memory_create = sub.add_parser("memory-create")
    memory_create.add_argument("--memory-id", required=True)
    memory_create.add_argument("--scope", choices=("task", "project", "mosh"), required=True)
    memory_create.add_argument("--scope-id")
    memory_create.add_argument("--classification", choices=("internal", "confidential", "restricted"), required=True)
    memory_create.add_argument("--summary", required=True)
    memory_create.add_argument("--source-kind", choices=("task", "event"), required=True)
    memory_create.add_argument("--source-id", required=True)
    memory_create.add_argument("--expires-at")
    memory_validate = sub.add_parser("memory-validate")
    memory_validate.add_argument("--memory-id", required=True)
    memory_validate.add_argument("--method", choices=("owner_review", "test", "cross_source"), required=True)
    memory_validate.add_argument("--evidence", action="append", required=True)
    memory_request = sub.add_parser("memory-request-approval")
    memory_request.add_argument("--memory-id", required=True)
    memory_request.add_argument("--by", default="owner")
    memory_decide = sub.add_parser("memory-decide")
    memory_decide.add_argument("--approval-id", required=True)
    memory_decide.add_argument("--decision", choices=("approved", "rejected"), required=True)
    memory_decide.add_argument("--by", default="owner")
    memory_decide.add_argument("--reason", required=True)
    memory_inspect = sub.add_parser("memory-inspect")
    memory_inspect.add_argument("--memory-id", required=True)
    memory_retrieve = sub.add_parser("memory-retrieve")
    memory_retrieve.add_argument("--scope", choices=("task", "project", "mosh"), required=True)
    memory_retrieve.add_argument("--scope-id")
    memory_retrieve.add_argument("--classification", choices=("internal", "confidential", "restricted"), required=True)
    memory_retrieve.add_argument("--by", default="owner")
    memory_retrieve.add_argument("--limit", type=int, default=50)
    memory_expire = sub.add_parser("memory-expire")
    memory_expire.add_argument("--memory-id", required=True)
    memory_expire.add_argument("--by", default="owner")
    memory_expire.add_argument("--reason", required=True)
    skill_create = sub.add_parser("skill-create")
    skill_create.add_argument("--skill-id", required=True)
    skill_create.add_argument("--name", required=True)
    skill_create.add_argument("--scope", choices=("agent", "project", "business", "core"), required=True)
    skill_create.add_argument("--scope-id")
    skill_create.add_argument("--risk", choices=tuple(item.value for item in Risk), required=True)
    skill_create.add_argument("--procedure-ref", required=True)
    skill_create.add_argument("--procedure-sha256", required=True)
    skill_create.add_argument("--evidence", action="append", required=True)
    skill_validate = sub.add_parser("skill-validate")
    skill_validate.add_argument("--skill-id", required=True)
    skill_validate.add_argument("--test", action="append", required=True)
    skill_request = sub.add_parser("skill-request-approval")
    skill_request.add_argument("--skill-id", required=True)
    skill_request.add_argument("--by", default="owner")
    skill_decide = sub.add_parser("skill-decide")
    skill_decide.add_argument("--approval-id", required=True)
    skill_decide.add_argument("--decision", choices=("approved", "rejected"), required=True)
    skill_decide.add_argument("--by", default="owner")
    skill_decide.add_argument("--reason", required=True)
    skill_inspect = sub.add_parser("skill-inspect")
    skill_inspect.add_argument("--skill-id", required=True)
    skill_retire = sub.add_parser("skill-retire")
    skill_retire.add_argument("--skill-id", required=True)
    skill_retire.add_argument("--by", default="owner")
    skill_retire.add_argument("--reason", required=True)
    task_memory_bind = sub.add_parser("task-memory-bind")
    task_memory_bind.add_argument("--task-id", required=True)
    task_memory_bind.add_argument("--memory-id", required=True)
    task_memory_bind.add_argument("--by", default="owner")
    task_memory_bind.add_argument("--reason", required=True)
    task_memory_list = sub.add_parser("task-memory-list")
    task_memory_list.add_argument("--task-id", required=True)
    args = parser.parse_args()

    if args.command == "init":
        with MoshRepository(args.db):
            pass
        print(json.dumps({"ok": True, "database": str(args.db.resolve())}))
        return 0

    if args.command == "summary":
        with MoshRepository(args.db) as repository:
            print(json.dumps(repository.summary(), indent=2))
        return 0

    if args.command in {"task-memory-bind", "task-memory-list"}:
        try:
            with MoshRepository(args.db) as repository:
                result = (
                    repository.bind_memory_to_task(args.task_id, args.memory_id, args.by, args.reason)
                    if args.command == "task-memory-bind"
                    else repository.list_task_memory_bindings(args.task_id)
                )
        except (KeyError, ValueError, sqlite3.IntegrityError) as error:
            print(json.dumps({"ok": False, "error": str(error)}))
            return 2
        print(json.dumps({"ok": True, "data": result}, indent=2))
        return 0

    if args.command.startswith("memory-"):
        try:
            with MoshRepository(args.db) as repository:
                if args.command == "memory-create":
                    result = repository.create_memory_candidate(
                        args.memory_id, args.scope, args.scope_id, args.classification, args.summary,
                        args.source_kind, args.source_id, args.expires_at,
                    )
                elif args.command == "memory-validate":
                    result = repository.validate_memory(args.memory_id, args.method, args.evidence)
                elif args.command == "memory-request-approval":
                    result = repository.request_memory_approval(args.memory_id, args.by)
                elif args.command == "memory-decide":
                    result = repository.decide_memory_approval(args.approval_id, args.decision, args.by, args.reason)
                elif args.command == "memory-retrieve":
                    result = repository.retrieve_approved_memory(
                        args.scope, args.scope_id, args.classification, args.by, args.limit
                    )
                elif args.command == "memory-expire":
                    result = repository.expire_memory(args.memory_id, args.by, args.reason)
                else:
                    result = repository.get_memory_record(args.memory_id)
        except (KeyError, ValueError, sqlite3.IntegrityError) as error:
            print(json.dumps({"ok": False, "error": str(error)}))
            return 2
        print(json.dumps({"ok": True, "data": result}, indent=2))
        return 0

    if args.command.startswith("skill-"):
        try:
            with MoshRepository(args.db) as repository:
                if args.command == "skill-create":
                    result = repository.create_skill_candidate(
                        args.skill_id, args.name, args.scope, args.scope_id, args.risk,
                        args.procedure_ref, args.procedure_sha256, args.evidence,
                    )
                elif args.command == "skill-validate":
                    result = repository.validate_skill(args.skill_id, args.test)
                elif args.command == "skill-request-approval":
                    result = repository.request_skill_approval(args.skill_id, args.by)
                elif args.command == "skill-decide":
                    result = repository.decide_skill_approval(args.approval_id, args.decision, args.by, args.reason)
                elif args.command == "skill-retire":
                    result = repository.retire_skill(args.skill_id, args.by, args.reason)
                else:
                    result = repository.get_skill_candidate(args.skill_id)
        except (KeyError, ValueError, sqlite3.IntegrityError) as error:
            print(json.dumps({"ok": False, "error": str(error)}))
            return 2
        print(json.dumps({"ok": True, "data": result}, indent=2))
        return 0

    if args.command == "backup-db":
        try:
            result = create_backup(args.db, args.output_dir)
        except ValueError as error:
            print(json.dumps({"ok": False, "error": str(error)}))
            return 2
        print(json.dumps({"ok": True, "data": result}, indent=2))
        return 0

    if args.command == "verify-backup":
        try:
            result = verify_backup(args.backup)
        except (ValueError, json.JSONDecodeError, sqlite3.DatabaseError) as error:
            print(json.dumps({"ok": False, "error": str(error)}))
            return 2
        print(json.dumps({"ok": True, "data": result}, indent=2))
        return 0

    if args.command == "n8n-discover":
        try:
            with MoshRepository(args.db) as repository:
                result = execute_approved_discovery(repository, args.request_id)
        except (ValueError, sqlite3.IntegrityError) as error:
            print(json.dumps({"ok": False, "error": str(error)}))
            return 2
        print(json.dumps({"ok": result["status"] == "completed", "data": result}, indent=2))
        return 0

    if args.command == "n8n-authorize-discovery":
        try:
            with MoshRepository(args.db) as repository:
                result = authorize_packet_file(
                    repository, args.packet, args.requested_by, args.approved_by
                )
        except (ValueError, sqlite3.IntegrityError) as error:
            print(json.dumps({"ok": False, "error": str(error)}))
            return 2
        print(json.dumps({"ok": True, "data": result}, indent=2))
        return 0

    if args.command == "n8n-discovery-status":
        try:
            with MoshRepository(args.db) as repository:
                governance = GovernedN8nDiscovery(repository)
                result = governance.inspect(args.request_id) if args.request_id else governance.status()
        except ValueError as error:
            print(json.dumps({"ok": False, "error": str(error)}))
            return 2
        print(json.dumps({"ok": True, "data": result}, indent=2))
        return 0

    if args.command == "serve-readonly":
        if not 1 <= args.port <= 65535:
            parser.error("--port must be 1..65535")
        try:
            TokenFile(args.token_file).read()
        except ValueError as error:
            parser.error(str(error))
        print(json.dumps({"ok": True, "bind": "127.0.0.1", "port": args.port, "read_only": True,
                          "token_file": str(args.token_file.resolve())}))
        serve(args.db, None, args.port, args.token_file)
        return 0

    if args.command == "serve-local":
        if not 1 <= args.port <= 65535:
            parser.error("--port must be 1..65535")
        try:
            TokenFile(args.token_file).read()
            TokenFile(args.write_token_file).read()
        except ValueError as error:
            parser.error(str(error))
        print(json.dumps({"ok": True, "bind": "127.0.0.1", "port": args.port, "read_only": False,
                          "token_file": str(args.token_file.resolve()),
                          "write_token_file": str(args.write_token_file.resolve())}))
        serve(args.db, None, args.port, args.token_file, write_token_file=args.write_token_file)
        return 0

    if args.command == "api-token-init":
        try:
            TokenFile(args.token_file).create(args.rotate)
        except (FileExistsError, ValueError) as error:
            parser.error(str(error))
        print(json.dumps({"ok": True, "token_file": str(args.token_file.resolve()), "rotated": args.rotate}))
        return 0

    if args.command == "adapter-acceptance":
        probes = [
            ClineAdapter().acceptance_probe(args.state_root),
            HermesAdapter().acceptance_probe(args.state_root),
        ]
        print(json.dumps({"probes": probes}, indent=2))
        return 0 if all(item["isolated_state"] and item["restart_readable"] for item in probes) else 1

    if args.command == "enqueue":
        account_id = f"{args.provider}-{args.account}"
        task = TaskEnvelope(
            task_id=args.task_id, project_id="MOSH-CORE", from_actor="owner", to=args.provider,
            objective=args.objective, provider=args.provider, account_id=account_id,
            acceptance=(f"{args.provider.title()} returns a result artifact",), constraints=("no auto-approved tools",),
            risk=Risk(args.risk), approval_required=args.approval_required,
        )
        with MoshRepository(args.db) as repository:
            stored = repository.create_task(task)
            run = repository.enqueue_run(stored.task_id)
        print(json.dumps({"task": stored.to_dict(), "run": run.to_dict()}, indent=2))
        return 0

    if args.command == "bind-cline-account":
        config = args.state_root.resolve() / "cline"
        if not (config / "data" / "db" / "sessions.db").exists():
            parser.error("Cline isolated session database not found; run acceptance and authenticate first")
        account = Account(
            account_id=f"cline-{args.alias}", provider="cline", alias=args.alias,
            credential_ref=f"cline-state:{args.alias}", status=AccountStatus.READY,
            metadata={"state_root": str(args.state_root.resolve())},
        )
        with MoshRepository(args.db) as repository:
            repository.register_account(account)
        print(json.dumps(account.to_dict(), indent=2))
        return 0

    if args.command == "bind-hermes-account":
        home = args.state_root.resolve() / "hermes"
        if not (home / "sessions").is_dir():
            parser.error("Hermes isolated session state not found; run acceptance and authenticate first")
        account = Account(
            account_id=f"hermes-{args.alias}", provider="hermes", alias=args.alias,
            credential_ref=f"hermes-state:{args.alias}", status=AccountStatus.READY,
            metadata={"state_root": str(args.state_root.resolve())},
        )
        with MoshRepository(args.db) as repository:
            repository.register_account(account)
        print(json.dumps(account.to_dict(), indent=2))
        return 0

    if args.command == "reroute":
        with MoshRepository(args.db) as repository:
            task = repository.reroute_task(args.task_id, f"codex-{args.account}", args.reason)
        print(json.dumps(task.to_dict(), indent=2))
        return 0

    if args.command == "worker-once":
        registry = args.accounts_root / "accounts.json"
        account_data = json.loads(registry.read_text(encoding="utf-8-sig"))
        executors = {
            f"codex-{item['alias']}": CodexAdapter(item["alias"], item["home"])
            for item in account_data.get("accounts", [])
        }
        cline = repository_account = None
        with MoshRepository(args.db) as repository:
            cline = repository.connection.execute(
                "SELECT account_id,alias,metadata_json FROM accounts WHERE provider='cline' AND status='ready' ORDER BY account_id LIMIT 1"
            ).fetchone()
            if cline:
                metadata = json.loads(cline["metadata_json"])
                executors[cline["account_id"]] = ClineAdapter(cline["alias"], metadata["state_root"])
            hermes = repository.connection.execute(
                "SELECT account_id,alias,metadata_json FROM accounts WHERE provider='hermes' AND status='ready' ORDER BY account_id LIMIT 1"
            ).fetchone()
            if hermes:
                metadata = json.loads(hermes["metadata_json"])
                executors[hermes["account_id"]] = HermesAdapter(hermes["alias"], metadata["state_root"])
            recovered = [run.to_dict() for run in repository.recover_expired_runs()]
            worked = Worker(repository, args.worker_id, executors).run_once()
            summary = repository.summary()
        print(json.dumps({"worked": worked, "recovered": recovered, "summary": summary}, indent=2))
        return 0


    if args.command == "task":
        with MoshRepository(args.db) as repository:
            task = repository.get_task(args.task_id)
            if task is None:
                parser.error(f"unknown task: {args.task_id}")
            runs = [dict(row) for row in repository.connection.execute(
                "SELECT * FROM task_runs WHERE task_id=? ORDER BY attempt", (args.task_id,)
            ).fetchall()]
            events = repository.list_events(args.task_id)
            approvals = [item.to_dict() for item in repository.list_approvals(args.task_id)]
            side_effects = [item.to_dict() for item in repository.list_side_effects(args.task_id)]
        print(json.dumps({"task": task.to_dict(), "runs": runs, "approvals": approvals, "side_effects": side_effects, "events": events}, indent=2))
        return 0

    if args.command == "cancel":
        with MoshRepository(args.db) as repository:
            task = repository.request_cancel(args.task_id, args.reason)
        print(json.dumps(task.to_dict(), indent=2))
        return 0

    if args.command in {"approve", "reject"}:
        with MoshRepository(args.db) as repository:
            approval = repository.decide_approval(
                args.task_id, args.command == "approve", args.by, args.reason
            )
        print(json.dumps(approval.to_dict(), indent=2))
        return 0

    if args.command == "request-side-effect":
        parameters = json.loads(args.parameters)
        if not isinstance(parameters, dict):
            parser.error("--parameters must be a JSON object")
        expires = (datetime.now(UTC) + timedelta(minutes=args.expires_minutes)).isoformat().replace("+00:00", "Z")
        with MoshRepository(args.db) as repository:
            request = repository.request_side_effect(
                args.task_id, args.action, args.target, parameters, Risk(args.risk), args.by, expires
            )
        print(json.dumps(request.to_dict(), indent=2))
        return 0

    if args.command in {"approve-side-effect", "reject-side-effect"}:
        try:
            with MoshRepository(args.db) as repository:
                request = repository.decide_side_effect(
                    args.request_id, args.digest, args.command == "approve-side-effect", args.by, args.reason
                )
        except (KeyError, ValueError) as error:
            print(json.dumps({"ok": False, "error": str(error)}))
            return 2
        print(json.dumps(request.to_dict(), indent=2))
        return 0

    if args.command == "consume-side-effect":
        parameters = json.loads(args.parameters)
        if not isinstance(parameters, dict):
            parser.error("--parameters must be a JSON object")
        try:
            with MoshRepository(args.db) as repository:
                request = repository.consume_side_effect(args.request_id, args.action, args.target, parameters)
        except (KeyError, ValueError) as error:
            print(json.dumps({"ok": False, "error": str(error)}))
            return 2
        print(json.dumps(request.to_dict(), indent=2))
        return 0

    if args.command in {"execute-local-marker", "recover-local-marker"}:
        try:
            with MoshRepository(args.db) as repository:
                executor = LocalMarkerExecutor(repository, args.root)
                execution = executor.execute(args.request_id) if args.command == "execute-local-marker" else executor.recover(args.execution_id)
        except (KeyError, ValueError, OSError) as error:
            print(json.dumps({"ok": False, "error": str(error)}))
            return 2
        print(json.dumps(execution.to_dict(), indent=2))
        return 0

    if args.command == "reconcile-local-markers":
        with MoshRepository(args.db) as repository:
            results = [item.to_dict() for item in LocalMarkerExecutor(repository, args.root).reconcile_all()]
        print(json.dumps({"reconciled": results}, indent=2))
        return 0 if all(item["status"] == "completed" for item in results) else 2

    if args.command == "list-side-effect-executions":
        with MoshRepository(args.db) as repository:
            executions = [item.to_dict() for item in repository.list_side_effect_executions()]
        print(json.dumps({"executions": executions}, indent=2))
        return 0

    if args.command == "plan-marker-cleanup":
        expires = (datetime.now(UTC) + timedelta(minutes=args.expires_minutes)).isoformat().replace("+00:00", "Z")
        retain_until = (datetime.now(UTC) + timedelta(days=args.retention_days)).isoformat().replace("+00:00", "Z")
        with MoshRepository(args.db) as repository:
            plan, request = MarkerCleanup(repository, args.root, args.trash_root).plan(
                args.task_id, args.execution_id, args.by, expires, retain_until
            )
        print(json.dumps({"plan": plan.to_dict(), "approval_request": request.to_dict()}, indent=2))
        return 0

    if args.command in {"execute-marker-cleanup", "request-marker-restore", "execute-marker-restore"}:
        try:
            with MoshRepository(args.db) as repository:
                cleanup = MarkerCleanup(repository, args.root, args.trash_root)
                if args.command == "execute-marker-cleanup":
                    result = cleanup.trash(args.plan_id).to_dict()
                elif args.command == "request-marker-restore":
                    expires = (datetime.now(UTC) + timedelta(minutes=args.expires_minutes)).isoformat().replace("+00:00", "Z")
                    result = cleanup.request_restore(args.plan_id, args.by, expires).to_dict()
                else:
                    result = cleanup.restore(args.plan_id).to_dict()
        except (KeyError, ValueError, OSError) as error:
            print(json.dumps({"ok": False, "error": str(error)}))
            return 2
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "list-cleanup-plans":
        with MoshRepository(args.db) as repository:
            plans = [item.to_dict() for item in repository.list_cleanup_plans()]
            operations = [item.to_dict() for item in repository.list_cleanup_operations()]
        print(json.dumps({"plans": plans, "operations": operations}, indent=2))
        return 0

    if args.command == "reconcile-marker-cleanup":
        with MoshRepository(args.db) as repository:
            results = [item.to_dict() for item in MarkerCleanup(repository, args.root, args.trash_root).reconcile_all()]
        print(json.dumps({"reconciled": results}, indent=2))
        return 0 if all(item["status"] == "completed" for item in results) else 2

    if args.command == "bootstrap":
        registry = args.accounts_root / "accounts.json"
        if not registry.exists():
            parser.error(f"account registry not found: {registry}")
        account_data = json.loads(registry.read_text(encoding="utf-8-sig"))
        adapters = [ClineAdapter(), HermesAdapter()]
        adapters.extend(CodexAdapter(item["alias"], item["home"]) for item in account_data.get("accounts", []))
        with ThreadPoolExecutor(max_workers=min(8, len(adapters))) as executor:
            capabilities = list(executor.map(lambda adapter: adapter.capabilities(), adapters))
        by_key = {(item["adapter"], item["details"].get("account_alias")): item for item in capabilities}
        with MoshRepository(args.db) as repository:
            for item in account_data.get("accounts", []):
                probe = by_key[("codex", item["alias"])]
                repository.register_account(Account(
                    account_id=f"codex-{item['alias']}", provider="codex", alias=item["alias"],
                    credential_ref=f"codex-home:{item['alias']}",
                    status=AccountStatus.READY if probe["healthy"] else AccountStatus.UNAVAILABLE,
                    metadata={"home": item["home"]},
                ))
            for adapter_name in ("codex", "cline", "hermes"):
                relevant = [item for item in capabilities if item["adapter"] == adapter_name]
                healthy = bool(relevant) and all(item["healthy"] for item in relevant)
                repository.register_agent(Agent(
                    agent_id=adapter_name, name=adapter_name.title(), adapter=adapter_name,
                    status=AgentStatus.READY if healthy else AgentStatus.DEGRADED,
                    capabilities=("health", "capabilities"), metadata={"probes": relevant},
                ))
            summary = repository.summary()
        print(json.dumps({"ok": True, "summary": summary, "capabilities": capabilities}, indent=2))
        return 0

    adapters = [ClineAdapter(), HermesAdapter()]
    registry = args.accounts_root / "accounts.json"
    if registry.exists():
        accounts = json.loads(registry.read_text(encoding="utf-8-sig"))
        for account in accounts.get("accounts", []):
            adapters.append(CodexAdapter(account["alias"], account["home"]))
    with ThreadPoolExecutor(max_workers=min(8, len(adapters))) as executor:
        results = list(executor.map(lambda adapter: adapter.capabilities(), adapters))
    print(json.dumps(results, indent=2))
    return 0 if all(item["healthy"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
