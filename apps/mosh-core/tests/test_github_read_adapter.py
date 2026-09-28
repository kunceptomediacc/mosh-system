import hashlib
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "mosh-core"))

from mosh_core.adapters.github_read import GitHubReadRequest, GovernedGitHubReadAdapter
from mosh_core.models import Agent
from mosh_core.repository import MoshRepository


class GitHubReadAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = MoshRepository(Path(self.temp.name) / "github.db")
        self.repo.register_agent(Agent("cline", "Cline", "cline"))
        self.repo.register_agent(Agent("codex", "Codex", "codex"))
        self.repo.register_plugin_definition(
            "github", "GitHub", "github", "github_app",
            ["repository.read", "pull_request.read"], ["metadata:read", "contents:read"],
        )
        self.repo.record_plugin_connection_check(
            "github", "connected", "healthy", ["metadata:read", "contents:read"],
        )
        self.repo.allow_plugin_agent("github", "cline", "owner")
        self.adapter = GovernedGitHubReadAdapter(self.repo, "cline")

    def tearDown(self):
        self.repo.close()
        self.temp.cleanup()

    def test_file_read_requires_allowlist_and_records_hash_only_audit(self):
        request = GitHubReadRequest("file.read", "owner/repo", "docs/README.md", 2, 20)
        with self.assertRaises(ValueError):
            self.adapter.authorize(request)
        self.adapter.allow_repository("owner/repo", "owner")
        authorized = self.adapter.authorize(request)
        self.assertEqual(authorized["path"], "docs/README.md")
        event = self.repo.connection.execute("SELECT * FROM plugin_read_events").fetchone()
        self.assertEqual(event["repository_sha256"], hashlib.sha256(b"owner/repo").hexdigest())
        self.assertEqual(event["resource_sha256"], hashlib.sha256(b"docs/README.md").hexdigest())
        self.assertNotIn("repository_full_name", event.keys())

    def test_non_owner_and_non_authorized_agent_are_rejected(self):
        with self.assertRaises(ValueError):
            self.adapter.allow_repository("owner/repo", "supervisor")
        self.adapter.allow_repository("owner/repo", "owner")
        with self.assertRaises(ValueError):
            GovernedGitHubReadAdapter(self.repo, "codex").authorize(
                GitHubReadRequest("repository.metadata", "owner/repo")
            )

    def test_write_traversal_and_unbounded_reads_are_rejected_without_audit(self):
        self.adapter.allow_repository("owner/repo", "owner")
        invalid = (
            GitHubReadRequest("file.write", "owner/repo", "README.md", 1, 2),
            GitHubReadRequest("file.read", "owner/repo", "../secret", 1, 2),
            GitHubReadRequest("file.read", "owner/repo", "README.md", 1, 501),
            GitHubReadRequest("file.read", "not-a-repo", "README.md", 1, 2),
        )
        for request in invalid:
            with self.assertRaises(ValueError):
                self.adapter.authorize(request)
        self.assertEqual(self.repo.connection.execute("SELECT count(*) FROM plugin_read_events").fetchone()[0], 0)

    def test_success_receipt_is_digest_only_bounded_and_immutable(self):
        self.adapter.allow_repository("owner/repo", "owner")
        event = self.adapter.authorize(GitHubReadRequest("file.read", "owner/repo", "README.md", 1, 5))
        receipt = self.adapter.record_success(event["audit_event_id"], "a" * 64, 120, 5)
        self.assertEqual(receipt["status"], "completed")
        self.assertEqual(receipt["response_sha256"], "a" * 64)
        with self.assertRaises(ValueError):
            self.adapter.record_success(event["audit_event_id"], "b" * 64, 120, 5)
        with self.assertRaises(ValueError):
            self.adapter.record_success(event["audit_event_id"], "not-a-digest", 120, 5)

    def test_failure_receipt_is_allowlisted_and_agent_bound(self):
        self.adapter.allow_repository("owner/repo", "owner")
        event = self.adapter.authorize(GitHubReadRequest("repository.metadata", "owner/repo"))
        with self.assertRaises(ValueError):
            self.adapter.record_failure(event["audit_event_id"], "raw private error text")
        with self.assertRaises(ValueError):
            GovernedGitHubReadAdapter(self.repo, "codex").record_failure(event["audit_event_id"], "not_found")
        receipt = self.adapter.record_failure(event["audit_event_id"], "not_found")
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["error_code"], "not_found")

    def test_expiring_grant_fails_closed_and_can_be_regranted(self):
        expiry = (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        first = self.adapter.allow_repository("owner/repo", "owner", expiry)
        self.assertEqual(first["expires_at"], expiry)
        self.repo.connection.execute(
            "UPDATE plugin_repository_access_grants SET expires_at='2000-01-01T00:00:00Z' WHERE grant_id=?",
            (first["grant_id"],),
        )
        with self.assertRaisesRegex(ValueError, "not allowlisted"):
            self.adapter.authorize(GitHubReadRequest("repository.metadata", "owner/repo"))
        second = self.adapter.allow_repository("owner/repo", "owner", expiry)
        self.assertNotEqual(first["grant_id"], second["grant_id"])
        self.assertEqual(self.adapter.authorize(
            GitHubReadRequest("repository.metadata", "owner/repo")
        )["operation"], "repository.metadata")

    def test_revocation_is_owner_only_reasoned_and_preserves_history(self):
        grant = self.adapter.allow_repository("owner/repo", "owner")
        with self.assertRaises(ValueError):
            self.adapter.revoke_repository("owner/repo", "operator", "requested")
        revoked = self.adapter.revoke_repository("owner/repo", "owner", "scope no longer required")
        self.assertEqual(revoked["grant_id"], grant["grant_id"])
        self.assertEqual(revoked["revoked_by"], "owner")
        self.assertEqual(revoked["revocation_reason"], "scope no longer required")
        with self.assertRaisesRegex(ValueError, "not allowlisted"):
            self.adapter.authorize(GitHubReadRequest("repository.metadata", "owner/repo"))
        replacement = self.adapter.allow_repository("owner/repo", "owner")
        self.assertNotEqual(replacement["grant_id"], grant["grant_id"])
        self.assertEqual(self.repo.connection.execute(
            "SELECT count(*) FROM plugin_repository_access_grants WHERE repository_full_name='owner/repo'"
        ).fetchone()[0], 2)

    def test_expiry_is_bounded_to_thirty_days(self):
        with self.assertRaises(ValueError):
            self.adapter.allow_repository("owner/repo", "owner", "not-a-date")
        too_late = (datetime.now(UTC) + timedelta(days=31)).isoformat().replace("+00:00", "Z")
        with self.assertRaises(ValueError):
            self.adapter.allow_repository("owner/repo", "owner", too_late)


if __name__ == "__main__":
    unittest.main()
