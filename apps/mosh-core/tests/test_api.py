import sys
import tempfile
import unittest
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "mosh-core"))

from mosh_core.api import DashboardAssets, ReadOnlyApi, TokenFile
from mosh_core.models import Account, AccountStatus, Agent, AgentStatus, Risk, TaskEnvelope, TaskStatus
from mosh_core.repository import MoshRepository
from mosh_core.auth_sessions import issue_session, validate_session


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "mosh.db"
        with MoshRepository(self.db) as repo:
            repo.register_account(Account("codex-one", "codex", "one", "secret-ref", AccountStatus.READY, {"home": "private"}))
            repo.register_account(Account("codex-two", "codex", "two", "other-secret", AccountStatus.READY))
            repo.register_agent(Agent("codex", "Codex", "codex", AgentStatus.READY, ("health", "capabilities"), {"private": True}))
            repo.create_task(TaskEnvelope("TASK-API", "MOSH", "owner", "local", "inspect"))
            repo.transition("TASK-API", TaskStatus.QUEUED, "queued for API test")
            repo.transition("TASK-API", TaskStatus.CLAIMED, "claimed for API test")
            repo.request_approval("TASK-API", "execute", "owner")
            repo.request_side_effect("TASK-API", "write_marker", "private-target", {"content": "private"}, Risk.HIGH, "agent", "2099-01-01T00:00:00Z")
        self.api = ReadOnlyApi(self.db, "0123456789abcdef")
        self.auth = "Bearer 0123456789abcdef"

    def tearDown(self):
        self.temp.cleanup()

    def test_authentication_and_read_only_method_gate(self):
        self.assertEqual(self.api.dispatch("GET", "/api/v1/health", None)[0], 401)
        status, body, headers = self.api.dispatch("POST", "/api/v1/tasks", self.auth)
        self.assertEqual(status, 405)
        self.assertEqual(headers["Allow"], "GET")
        self.assertEqual(body["error"]["code"], "method_not_allowed")

    def test_google_session_authentication_and_logout_revocation(self):
        with MoshRepository(self.db) as repo:
            repo.register_identity_provider("local", "local_dev", "mosh://local-development", None, "owner")
            principal = repo.register_identity_principal("local", "b" * 64, "Owner", "owner")
            repo.grant_workspace_role(principal["principal_id"], "owner", "owner")
        token, _ = issue_session(self.db, principal["principal_id"])
        cookie = f"mosh_session={token}"
        self.assertEqual(self.api.dispatch("GET", "/api/v1/health", None, cookie_header=cookie)[0], 200)
        me = self.api.dispatch("GET", "/api/v1/me", None, cookie_header=cookie)
        self.assertEqual(me[1]["data"]["display_alias"], "Owner")
        self.assertEqual(me[1]["data"]["roles"], ["owner"])
        self.assertEqual(me[1]["data"]["authentication"], "google_session")
        status, body, headers = self.api.dispatch("POST", "/api/v1/logout", None, cookie_header=cookie)
        self.assertEqual(status, 200)
        self.assertTrue(body["data"]["signed_out"])
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertFalse(validate_session(self.db, token))

        expired = self.api.dispatch("GET", "/api/v1/me", None, cookie_header=cookie)
        self.assertEqual(expired[0], 401)
        self.assertEqual(expired[1]["error"]["code"], "session_expired")
        self.assertIn("Max-Age=0", expired[2]["Set-Cookie"])

    def test_owner_session_management_lists_safe_metadata_and_revokes_another_session(self):
        with MoshRepository(self.db) as repo:
            repo.register_identity_provider("local", "local_dev", "mosh://local-development", None, "owner")
            owner = repo.register_identity_principal("local", "c" * 64, "Owner", "owner")
            repo.grant_workspace_role(owner["principal_id"], "owner", "owner")
        current_token, _ = issue_session(self.db, owner["principal_id"])
        other_token, _ = issue_session(self.db, owner["principal_id"])
        current_cookie = f"mosh_session={current_token}"

        status, body, _ = self.api.dispatch("GET", "/api/v1/sessions", None, cookie_header=current_cookie)
        self.assertEqual(status, 200)
        sessions = body["data"]
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sum(1 for item in sessions if item["current"]), 1)
        self.assertTrue(all(set(item) == {
            "session_id", "principal_id", "display_alias", "created_at", "expires_at", "current"
        } for item in sessions))
        self.assertNotIn("token_sha256", json.dumps(sessions))

        current_id = next(item["session_id"] for item in sessions if item["current"])
        other_id = next(item["session_id"] for item in sessions if not item["current"])
        denied = self.api.dispatch("POST", f"/api/v1/sessions/{current_id}/revoke", None,
                                   cookie_header=current_cookie)
        self.assertEqual(denied[0], 409)
        self.assertTrue(validate_session(self.db, current_token))

        revoked = self.api.dispatch("POST", f"/api/v1/sessions/{other_id}/revoke", None,
                                    cookie_header=current_cookie)
        self.assertEqual(revoked[0], 200)
        self.assertTrue(revoked[1]["data"]["revoked"])
        self.assertFalse(validate_session(self.db, other_token))

    def test_owner_can_revoke_all_sessions_for_principal_and_non_owner_is_denied(self):
        with MoshRepository(self.db) as repo:
            repo.register_identity_provider("local", "local_dev", "mosh://local-development", None, "owner")
            owner = repo.register_identity_principal("local", "d" * 64, "Owner", "owner")
            viewer = repo.register_identity_principal("local", "e" * 64, "Viewer", "owner")
            repo.grant_workspace_role(owner["principal_id"], "owner", "owner")
            repo.grant_workspace_role(viewer["principal_id"], "viewer", "owner")
        owner_token, _ = issue_session(self.db, owner["principal_id"])
        viewer_token_one, _ = issue_session(self.db, viewer["principal_id"])
        viewer_token_two, _ = issue_session(self.db, viewer["principal_id"])

        denied = self.api.dispatch("GET", "/api/v1/sessions", None,
                                   cookie_header=f"mosh_session={viewer_token_one}")
        self.assertEqual(denied[0], 403)
        self.assertEqual(denied[1]["error"]["code"], "owner_required")

        revoked = self.api.dispatch(
            "POST", f"/api/v1/principals/{viewer['principal_id']}/sessions/revoke-all", None,
            cookie_header=f"mosh_session={owner_token}",
        )
        self.assertEqual(revoked[0], 200)
        self.assertEqual(revoked[1]["data"]["revoked_count"], 2)
        self.assertFalse(validate_session(self.db, viewer_token_one))
        self.assertFalse(validate_session(self.db, viewer_token_two))
        self.assertTrue(validate_session(self.db, owner_token))

        missing = self.api.dispatch(
            "POST", "/api/v1/principals/IDP-MISSING/sessions/revoke-all", None,
            cookie_header=f"mosh_session={owner_token}",
        )
        self.assertEqual(missing[0], 404)

        self_revoked = self.api.dispatch(
            "POST", f"/api/v1/principals/{owner['principal_id']}/sessions/revoke-all", None,
            cookie_header=f"mosh_session={owner_token}",
        )
        self.assertEqual(self_revoked[0], 200)
        self.assertEqual(self_revoked[1]["data"]["revoked_count"], 1)
        self.assertIn("Max-Age=0", self_revoked[2]["Set-Cookie"])
        self.assertFalse(validate_session(self.db, owner_token))

    def test_task_submission_requires_separate_write_token_and_explicit_account(self):
        api = ReadOnlyApi(self.db, "0123456789abcdef", write_token="fedcba9876543210")
        body = json.dumps({"objective": "Draft a bounded implementation plan", "account_id": "codex-two", "risk": "low"}).encode()
        denied = api.dispatch("POST", "/api/v1/tasks", self.auth, body=body)
        self.assertEqual(denied[0], 403)
        self.assertEqual(denied[1]["error"]["code"], "write_token_required")
        created = api.dispatch("POST", "/api/v1/tasks", self.auth, "fedcba9876543210", body)
        self.assertEqual(created[0], 201)
        self.assertTrue(created[2]["Location"].startswith("/api/v1/tasks/TASK-UI-"))
        task = created[1]["data"]["task"]
        self.assertEqual(task["account_id"], "codex-two")
        self.assertEqual(task["provider"], "codex")
        self.assertTrue(task["approval_required"])
        self.assertEqual(task["status"], "queued")
        self.assertEqual(created[1]["data"]["run"]["status"], "queued")
        with MoshRepository(self.db) as repo:
            approvals = repo.list_approvals(task["task_id"])
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0].decision.value, "pending")

    def test_task_submission_validation_is_bounded_and_does_not_trust_provider(self):
        api = ReadOnlyApi(self.db, "0123456789abcdef", write_token="fedcba9876543210")
        def post(value):
            return api.dispatch("POST", "/api/v1/tasks", self.auth, "fedcba9876543210", json.dumps(value).encode())
        self.assertEqual(post({"objective": "", "account_id": "codex-one", "risk": "low"})[0], 422)
        self.assertEqual(post({"objective": "x" * 4001, "account_id": "codex-one", "risk": "low"})[0], 422)
        self.assertEqual(post({"objective": "ok", "account_id": "missing", "risk": "low"})[0], 422)
        self.assertEqual(post({"objective": "ok", "account_id": "codex-one", "risk": "extreme"})[0], 422)
        self.assertEqual(post({"objective": "ok", "account_id": "codex-one", "risk": "low", "provider": "hermes"})[0], 422)
        self.assertEqual(api.dispatch("POST", "/api/v1/tasks", self.auth, "fedcba9876543210", b"not-json")[0], 400)

    def test_task_submission_rate_limit_applies_only_after_write_authentication(self):
        api = ReadOnlyApi(self.db, "0123456789abcdef", write_token="fedcba9876543210")
        body = json.dumps({"objective": "bounded", "account_id": "codex-one", "risk": "low"}).encode()
        for _ in range(20):
            self.assertEqual(api.dispatch("POST", "/api/v1/tasks", self.auth, "wrong-write-token", body)[0], 403)
        for _ in range(10):
            self.assertEqual(api.dispatch("POST", "/api/v1/tasks", self.auth, "fedcba9876543210", body)[0], 201)
        limited = api.dispatch("POST", "/api/v1/tasks", self.auth, "fedcba9876543210", body)
        self.assertEqual(limited[0], 429)
        self.assertEqual(limited[2]["Retry-After"], "60")

    def test_task_collection_detail_and_errors(self):
        status, body, _ = self.api.dispatch("GET", "/api/v1/tasks?limit=10&offset=0", self.auth)
        self.assertEqual(status, 200)
        self.assertEqual(body["meta"]["total"], 1)
        self.assertEqual(body["data"][0]["task_id"], "TASK-API")
        detail = self.api.dispatch("GET", "/api/v1/tasks/TASK-API", self.auth)
        self.assertEqual(detail[0], 200)
        self.assertNotIn("events", detail[1]["data"])
        self.assertEqual(self.api.dispatch("GET", "/api/v1/tasks/missing", self.auth)[0], 404)
        self.assertEqual(self.api.dispatch("GET", "/api/v1/tasks?limit=999", self.auth)[0], 400)

    def test_project_overview_is_derived_and_paginated(self):
        status, body, _ = self.api.dispatch("GET", "/api/v1/projects?limit=10&offset=0", self.auth)
        self.assertEqual(status, 200)
        self.assertEqual(body["meta"]["total"], 1)
        self.assertEqual(body["data"][0]["project_id"], "MOSH")
        self.assertEqual(body["data"][0]["total_tasks"], 1)
        self.assertEqual(body["data"][0]["active_tasks"], 1)
        self.assertIn("last_updated", body["data"][0])
        self.assertEqual(self.api.dispatch("GET", "/api/v1/projects?limit=101", self.auth)[0], 400)

    def test_workspace_view_is_allowlisted_and_path_free(self):
        root = Path(self.temp.name) / "workspace"
        (root / "apps" / "demo").mkdir(parents=True)
        (root / "apps" / "demo" / "main.py").write_text("pass", encoding="utf-8")
        (root / ".git-init-backup").mkdir()
        api = ReadOnlyApi(self.db, "0123456789abcdef", workspace_root=root)
        status, body, _ = api.dispatch("GET", "/api/v1/workspace", self.auth)
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["workspace"], "workspace")
        self.assertEqual(body["data"]["git"]["state"], "metadata_parked")
        apps = next(item for item in body["data"]["areas"] if item["name"] == "apps")
        self.assertEqual(apps["file_count"], 1)
        encoded = json.dumps(body)
        self.assertNotIn(str(root), encoded)
        self.assertNotIn("main.py", encoded)

    def test_integrations_view_is_aggregate_only_and_path_free(self):
        root = Path(self.temp.name) / "workspace"
        (root / "plugins" / "installed" / "secret-plugin-name").mkdir(parents=True)
        (root / "tools" / "mcp").mkdir(parents=True)
        (root / "tools" / "mcp" / "private-config.json").write_text("{}", encoding="utf-8")
        api = ReadOnlyApi(self.db, "0123456789abcdef", workspace_root=root)
        status, body, _ = api.dispatch("GET", "/api/v1/integrations", self.auth)
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["agents"], {"registered": 1, "ready": 1})
        self.assertEqual(body["data"]["capabilities"], [
            {"name": "capabilities", "agent_count": 1},
            {"name": "health", "agent_count": 1},
        ])
        installed = next(item for item in body["data"]["areas"] if item["kind"] == "plugins_installed")
        self.assertEqual(installed, {"kind": "plugins_installed", "present": True, "entry_count": 1})
        encoded = json.dumps(body)
        self.assertNotIn(str(root), encoded)
        self.assertNotIn("secret-plugin-name", encoded)
        self.assertNotIn("private-config.json", encoded)

    def test_plugins_view_is_read_only_and_credential_free(self):
        with MoshRepository(self.db) as repo:
            repo.register_plugin_definition(
                "github", "GitHub", "github", "github_app",
                ["repository.read", "pull_request.read"], ["metadata:read", "contents:read"],
            )
        status, body, _ = self.api.dispatch("GET", "/api/v1/plugins", self.auth)
        self.assertEqual(status, 200)
        self.assertEqual(body["data"][0]["status"], "unconfigured")
        self.assertEqual(body["data"][0]["granted_scopes"], [])
        self.assertEqual(body["data"][0]["allowed_agents"], [])
        self.assertTrue(body["data"][0]["approval_required_for_writes"])
        encoded = json.dumps(body).lower()
        for prohibited in ("credential", "secret", "token_ref", "access_token"):
            self.assertNotIn(prohibited, encoded)

    def test_plugin_activity_is_aggregate_only(self):
        with MoshRepository(self.db) as repo:
            repo.connection.execute(
                "INSERT INTO plugin_registrations(plugin_id,name,provider,status,auth_type,health_status,created_at,updated_at) VALUES('github','GitHub','github','connected','github_app','healthy','now','now')"
            )
            repo.connection.execute(
                "INSERT INTO plugin_repository_access_grants(plugin_id,repository_full_name,allowed_by,created_at) VALUES('github','private-owner/private-repo','owner','now')"
            )
            repo.connection.execute(
                "INSERT INTO plugin_read_events(plugin_id,agent_id,operation,repository_sha256,resource_sha256,start_line,end_line,created_at) VALUES('github','codex','file.read',?,? ,1,5,'now')",
                ("a" * 64, "b" * 64),
            )
            event_id = repo.connection.execute("SELECT max(event_id) FROM plugin_read_events").fetchone()[0]
            repo.connection.execute(
                "INSERT INTO plugin_read_outcomes(event_id,status,response_sha256,returned_bytes,returned_lines,completed_at) VALUES(?,'completed',?,100,5,'now')",
                (event_id, "c" * 64),
            )
        status, body, _ = self.api.dispatch("GET", "/api/v1/plugin-activity", self.auth)
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["allowlisted_repositories"], 1)
        self.assertEqual(body["data"]["reads"], {"authorized": 1, "completed": 1, "failed": 0, "pending": 0})
        encoded = json.dumps(body)
        for prohibited in ("private-owner", "private-repo", "a" * 64, "b" * 64, "c" * 64):
            self.assertNotIn(prohibited, encoded)

    def test_governance_status_is_aggregate_and_makes_no_compliance_claim(self):
        with MoshRepository(self.db) as repo:
            repo.configure_project_governance(
                "PRIVATE-CLIENT", "business", "restricted", "private-client-2026", "owner",
            )
        status, body, _ = self.api.dispatch("GET", "/api/v1/governance-status", self.auth)
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["profiles"]["business"], 1)
        self.assertEqual(body["data"]["classifications"]["restricted"], 1)
        self.assertEqual(body["data"]["compliance_claims"], "prohibited")
        encoded = json.dumps(body)
        self.assertNotIn("PRIVATE-CLIENT", encoded)
        self.assertNotIn("private-client", encoded)

    def test_memory_stores_view_exposes_metadata_not_content(self):
        status, body, _ = self.api.dispatch("GET", "/api/v1/memory-stores", self.auth)
        self.assertEqual(status, 200)
        data = body["data"]
        self.assertEqual(data["status"], "healthy")
        self.assertGreaterEqual(data["schema_version"], 1)
        stores = {item["name"]: item for item in data["stores"]}
        self.assertEqual(stores["task_context"]["record_count"], 1)
        self.assertGreaterEqual(stores["audit_events"]["record_count"], 1)
        self.assertEqual(stores["approval_history"]["record_count"], 1)
        encoded = json.dumps(body)
        self.assertNotIn("inspect", encoded)
        self.assertNotIn("private-target", encoded)
        self.assertNotIn("private", encoded)

    def test_log_streams_view_is_categorized_and_payload_free(self):
        status, body, _ = self.api.dispatch("GET", "/api/v1/log-streams", self.auth)
        self.assertEqual(status, 200)
        data = body["data"]
        self.assertEqual(data["status"], "available")
        self.assertGreaterEqual(data["total_events"], 5)
        streams = {item["name"]: item for item in data["streams"]}
        self.assertGreaterEqual(streams["task"]["event_count"], 3)
        self.assertEqual(streams["approval"]["event_count"], 1)
        self.assertEqual(streams["side_effect"]["event_count"], 1)
        self.assertEqual(data["signals"], {"failures": 0, "cancellations": 0, "recoveries": 0})
        encoded = json.dumps(body)
        self.assertNotIn("queued for API test", encoded)
        self.assertNotIn("private-target", encoded)
        self.assertNotIn("payload", encoded)

    def test_system_status_requires_multi_account_resilience_and_is_aggregate_only(self):
        status, body, _ = self.api.dispatch("GET", "/api/v1/system-status", self.auth)
        self.assertEqual(status, 200)
        data = body["data"]
        self.assertEqual(data["status"], "operational")
        components = {item["name"]: item for item in data["components"]}
        self.assertEqual(components["accounts"]["ready"], 2)
        self.assertEqual(components["routing_resilience"], {"name": "routing_resilience", "status": "ready", "ready": 2, "total": 2})
        self.assertEqual(data["work"], {"active_tasks": 1, "attention_tasks": 0})
        self.assertEqual(data["gates"], {"pending_approvals": 1, "pending_side_effects": 1})
        encoded = json.dumps(body)
        self.assertNotIn("codex-one", encoded)
        self.assertNotIn("codex-two", encoded)
        self.assertNotIn("TASK-API", encoded)
        self.assertNotIn("inspect", encoded)

    def test_learning_status_is_aggregate_only_and_embeddings_are_disabled(self):
        with MoshRepository(self.db) as repo:
            repo.create_memory_candidate(
                "MEM-PRIVATE-0001", "task", "TASK-API", "internal",
                "A private governed summary.", "task", "TASK-API",
            )
            repo.create_skill_candidate(
                "SKILL-PRIVATE-0001", "Private skill name", "project", "MOSH", "low",
                "skills/projects/private/SKILL.md", "f" * 64, ["TASK-API"],
            )
        status, body, _ = self.api.dispatch("GET", "/api/v1/learning-status", self.auth)
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["memory"]["candidate"], 1)
        self.assertEqual(body["data"]["skills"]["candidate"], 1)
        self.assertEqual(body["data"]["task_bindings"], {"total": 0, "usable": 0})
        self.assertEqual(body["data"]["embeddings"], {"status": "disabled", "indexed_records": 0})
        encoded = json.dumps(body)
        for prohibited in ("private governed", "Private skill", "skills/projects", "TASK-API", "evidence"):
            self.assertNotIn(prohibited, encoded)

    def test_token_minimum_length(self):
        with self.assertRaises(ValueError):
            ReadOnlyApi(self.db, "short")

    def test_event_cursor_pagination(self):
        status, first, _ = self.api.dispatch("GET", "/api/v1/tasks/TASK-API/events?limit=2", self.auth)
        self.assertEqual(status, 200)
        self.assertEqual(len(first["data"]), 2)
        self.assertTrue(first["meta"]["has_next"])
        event_ids = [item["event_id"] for item in first["data"]]
        page = first
        while page["meta"]["has_next"]:
            cursor = page["meta"]["next_cursor"]
            status, page, _ = self.api.dispatch(
                "GET", f"/api/v1/tasks/TASK-API/events?limit=2&after_event_id={cursor}", self.auth
            )
            self.assertEqual(status, 200)
            event_ids.extend(item["event_id"] for item in page["data"])
        self.assertEqual(event_ids, sorted(set(event_ids)))
        self.assertGreaterEqual(len(event_ids), 5)
        self.assertEqual(self.api.dispatch("GET", "/api/v1/tasks/TASK-API/events?limit=101", self.auth)[0], 400)

    def test_token_file_live_rotation(self):
        path = Path(self.temp.name) / "api-token"
        source = TokenFile(path)
        source.create()
        old_token = source.read()
        api = ReadOnlyApi(self.db, token_file=path)
        self.assertEqual(api.dispatch("GET", "/api/v1/health", f"Bearer {old_token}")[0], 200)
        source.create(rotate=True)
        new_token = source.read()
        self.assertNotEqual(old_token, new_token)
        self.assertEqual(api.dispatch("GET", "/api/v1/health", f"Bearer {old_token}")[0], 401)
        self.assertEqual(api.dispatch("GET", "/api/v1/health", f"Bearer {new_token}")[0], 200)

    def test_dashboard_assets_are_allowlisted(self):
        root = Path(self.temp.name) / "ui"
        root.mkdir()
        (root / "index.html").write_text("<main>MOSH</main>", encoding="utf-8")
        assets = DashboardAssets(root)
        body, content_type = assets.read("/")  # type: ignore[misc]
        self.assertEqual(body, b"<main>MOSH</main>")
        self.assertEqual(content_type, "text/html; charset=utf-8")
        self.assertIsNone(assets.read("/../secret"))

    def test_dashboard_packaging_assets_are_explicitly_allowlisted(self):
        assets = DashboardAssets(ROOT / "apps" / "mosh-ui")
        for route in ("/manifest.webmanifest", "/sw.js", "/icon.svg"):
            body, content_type = assets.read(route)  # type: ignore[misc]
            self.assertTrue(body)
            self.assertIn("charset=utf-8", content_type)
        service_worker = assets.read("/sw.js")[0].decode("utf-8")  # type: ignore[index]
        self.assertIn('url.pathname.startsWith("/api/")', service_worker)
        self.assertNotIn("Authorization", service_worker)

    def test_dashboard_session_ui_has_lock_logout_and_expiry_paths(self):
        application = (ROOT / "apps" / "mosh-ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn('error.code = body?.error?.code || "request_failed"', application)
        self.assertIn("function lockDashboard", application)
        self.assertIn('error.code === "session_expired"', application)
        self.assertIn('api("/api/v1/logout", { method: "POST" })', application)
        self.assertIn('`${me.data.display_alias} · ${me.data.roles.join(", ")}`', application)

    def test_operational_views_redact_sensitive_fields(self):
        accounts = self.api.dispatch("GET", "/api/v1/accounts", self.auth)
        self.assertEqual(accounts[0], 200)
        self.assertEqual([item["account_id"] for item in accounts[1]["data"]], ["codex-one", "codex-two"])
        self.assertNotIn("credential_ref", accounts[1]["data"][0])
        self.assertNotIn("metadata", accounts[1]["data"][0])
        agents = self.api.dispatch("GET", "/api/v1/agents", self.auth)
        self.assertEqual(agents[1]["data"][0]["capabilities"], ["health", "capabilities"])
        self.assertNotIn("metadata", agents[1]["data"][0])
        approvals = self.api.dispatch("GET", "/api/v1/approvals?decision=pending", self.auth)
        self.assertEqual(approvals[1]["meta"]["total"], 1)
        effects = self.api.dispatch("GET", "/api/v1/side-effects?status=pending", self.auth)
        self.assertEqual(effects[1]["meta"]["total"], 1)
        for redacted in ("target", "parameters", "request_digest"):
            self.assertNotIn(redacted, effects[1]["data"][0])
        self.assertEqual(self.api.dispatch("GET", "/api/v1/approvals?decision=nope", self.auth)[0], 400)


if __name__ == "__main__":
    unittest.main()
