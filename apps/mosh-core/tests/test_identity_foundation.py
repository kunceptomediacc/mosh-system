import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "mosh-core"))

from mosh_core.identity import GoogleSignInAdapter, LocalDevelopmentIdentity
from mosh_core.rbac import effective_permissions, has_permission
from mosh_core.repository import MoshRepository


class IdentityFoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = MoshRepository(Path(self.temp.name) / "identity.db")

    def tearDown(self):
        self.repo.close()
        self.temp.cleanup()

    def test_local_identity_fails_closed_outside_development(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                LocalDevelopmentIdentity().authenticate("owner")
        with patch.dict(os.environ, {"MOSH_ENV": "development"}, clear=True):
            claim = LocalDevelopmentIdentity().authenticate("owner")
        self.assertTrue(claim["development_only"])
        self.assertEqual(len(claim["subject_sha256"]), 64)

    def test_google_authorization_uses_oidc_state_nonce_pkce_and_account_selection(self):
        request = GoogleSignInAdapter("client.apps.googleusercontent.com").authorization_request("http://127.0.0.1:1455/auth/callback")
        query = parse_qs(urlparse(request.url).query)
        self.assertEqual(query["scope"], ["openid profile email"])
        self.assertEqual(query["prompt"], ["select_account"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["state"], [request.state])
        self.assertEqual(query["nonce"], [request.nonce])
        self.assertNotIn(request.code_verifier, request.url)

    def test_provider_principal_and_rbac_are_owner_governed_and_publicly_aggregate(self):
        provider = self.repo.register_identity_provider("local-dev", "local_dev", "mosh://local-development", None, "owner")
        self.assertEqual(provider["development_only"], 1)
        principal = self.repo.register_identity_principal("local-dev", hashlib.sha256(b"local:owner").hexdigest(), "Owner", "owner")
        self.repo.grant_workspace_role(principal["principal_id"], "owner", "owner")
        status = self.repo.identity_status_public()
        self.assertEqual(status["providers"]["local_dev"], 1)
        self.assertEqual(status["active_principals"], 1)
        self.assertEqual(status["roles"]["owner"], 1)
        self.assertNotIn(principal["principal_id"], str(status))

    def test_rbac_is_deny_by_default_and_reserves_sensitive_permissions(self):
        self.assertTrue(has_permission({"viewer"}, "dashboard.read"))
        self.assertFalse(has_permission({"viewer"}, "task.submit"))
        self.assertFalse(has_permission({"admin"}, "identity.manage"))
        self.assertFalse(has_permission({"admin"}, "publish.request"))
        self.assertTrue(has_permission({"owner"}, "publish.request"))
        self.assertEqual(effective_permissions({"unknown"}), frozenset())


if __name__ == "__main__":
    unittest.main()
