import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "mosh-core"))

from mosh_core.models import Agent
from mosh_core.db import MIGRATIONS, connect, migrate
from mosh_core.repository import MoshRepository


class Phase5PluginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = MoshRepository(Path(self.temp.name) / "plugins.db")

    def tearDown(self):
        self.repo.close()
        self.temp.cleanup()

    def test_forward_migration_adds_credential_free_plugin_tables(self):
        version = self.repo.connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
        self.assertGreaterEqual(version, 14)
        columns = {row[1] for row in self.repo.connection.execute("PRAGMA table_info(plugin_registrations)")}
        self.assertFalse({"credential", "secret", "token", "credential_ref"} & columns)
        self.assertIn("plugin_agent_access", {
            row[0] for row in self.repo.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        })
        grant_columns = {
            row[1] for row in self.repo.connection.execute("PRAGMA table_info(plugin_repository_access_grants)")
        }
        self.assertTrue({"expires_at", "revoked_at", "revoked_by", "revocation_reason"}.issubset(grant_columns))
        self.assertFalse({"credential", "secret", "token"} & grant_columns)

    def test_repository_grant_migration_backfills_legacy_access_without_expiry(self):
        database = Path(self.temp.name) / "legacy-plugins.db"
        connection = connect(database)
        try:
            for version, script in enumerate(MIGRATIONS[:26], start=1):
                connection.executescript("BEGIN IMMEDIATE;\n" + script)
                connection.execute(
                    "INSERT INTO schema_migrations(version,applied_at) VALUES(?, '2026-01-01T00:00:00Z')",
                    (version,),
                )
                connection.commit()
            connection.execute(
                """INSERT INTO plugin_registrations(
                       plugin_id,name,provider,status,auth_type,health_status,created_at,updated_at
                   ) VALUES('github','GitHub','github','connected','github_app','healthy','now','now')"""
            )
            connection.execute(
                """INSERT INTO plugin_repository_access(
                       plugin_id,repository_full_name,allowed_by,created_at
                   ) VALUES('github','owner/legacy','owner','2026-01-01T00:00:00Z')"""
            )
            migrate(connection)
            grant = connection.execute(
                "SELECT * FROM plugin_repository_access_grants WHERE repository_full_name='owner/legacy'"
            ).fetchone()
            self.assertIsNotNone(grant)
            self.assertIsNone(grant["expires_at"])
            self.assertIsNone(grant["revoked_at"])
            self.assertEqual(connection.execute(
                "SELECT max(version) FROM schema_migrations"
            ).fetchone()[0], len(MIGRATIONS))
        finally:
            connection.close()

    def test_registration_is_unconfigured_and_preserves_explicit_connection_state(self):
        created = self.repo.register_plugin_definition(
            "github", "GitHub", "github", "github_app",
            ["repository.read"], ["metadata:read", "contents:read"],
        )
        self.assertEqual(created["status"], "unconfigured")
        self.assertEqual(created["granted_scopes"], [])
        self.assertEqual(created["allowed_agents"], [])
        self.assertTrue(created["approval_required_for_writes"])
        self.repo.connection.execute(
            "UPDATE plugin_registrations SET status='disabled',health_status='unavailable' WHERE plugin_id='github'"
        )
        updated = self.repo.register_plugin_definition(
            "github", "GitHub", "github", "github_app", ["repository.read"], ["metadata:read"],
        )
        self.assertEqual(updated["status"], "disabled")
        self.assertEqual(updated["health_status"], "unavailable")

    def test_registration_rejects_unbounded_identifiers_and_scope_values(self):
        invalid = (
            ("GitHub Upper", ["metadata:read"]),
            ("github", ["scope with spaces"]),
        )
        for plugin_id, scopes in invalid:
            with self.assertRaises(ValueError):
                self.repo.register_plugin_definition(plugin_id, "GitHub", "github", "oauth", [], scopes)
        self.assertEqual(self.repo.list_plugins_public(), [])

    def test_connection_check_records_only_observed_subset_and_never_grants_agents(self):
        self.repo.register_plugin_definition(
            "github", "GitHub", "github", "github_app",
            ["repository.read"], ["metadata:read", "contents:read"],
        )
        connected = self.repo.record_plugin_connection_check("github", "connected", "healthy", [])
        self.assertEqual(connected["status"], "connected")
        self.assertEqual(connected["health_status"], "healthy")
        self.assertEqual(connected["granted_scopes"], [])
        self.assertEqual(connected["allowed_agents"], [])
        self.assertIsNotNone(connected["last_checked_at"])
        with self.assertRaises(ValueError):
            self.repo.record_plugin_connection_check("github", "connected", "degraded", [])
        with self.assertRaises(ValueError):
            self.repo.record_plugin_connection_check("github", "connected", "healthy", ["administration:write"])

    def test_agent_access_requires_owner_registered_agent_and_verified_scope(self):
        self.repo.register_plugin_definition(
            "github", "GitHub", "github", "github_app", ["repository.read"], ["metadata:read"],
        )
        self.repo.register_agent(Agent("codex", "Codex", "codex"))
        self.repo.record_plugin_connection_check("github", "connected", "healthy", ["metadata:read"])
        with self.assertRaises(ValueError):
            self.repo.allow_plugin_agent("github", "codex", "supervisor")
        with self.assertRaises(ValueError):
            self.repo.allow_plugin_agent("github", "missing", "owner")
        allowed = self.repo.allow_plugin_agent("github", "codex", "owner")
        self.assertEqual(allowed["allowed_agents"], ["codex"])
        self.assertEqual(allowed["granted_scopes"], ["metadata:read"])

    def test_contract_is_closed_and_contains_no_secret_field(self):
        schema = json.loads((ROOT / "bridge" / "contracts" / "v1" / "plugin-registration.schema.json").read_text())
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["approval_required_for_writes"], {"const": True})
        self.assertNotIn("credential", json.dumps(schema).lower())


if __name__ == "__main__":
    unittest.main()
