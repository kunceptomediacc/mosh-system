import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "mosh-core"))

from mosh_core.google_signin import credential_failure_code, login_page, verify_google_credential
from mosh_core.auth_sessions import issue_session, revoke_session, validate_session
from mosh_core.repository import MoshRepository


class GoogleSignInTests(unittest.TestCase):
    def test_session_requires_role_and_is_stored_as_digest(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "mosh.db"
            with MoshRepository(database) as repo:
                repo.register_identity_provider("local", "local_dev", "mosh://local-development", None, "owner")
                principal = repo.register_identity_principal("local", "a" * 64, "Owner", "owner")
                repo.grant_workspace_role(principal["principal_id"], "owner", "owner")
            token, _ = issue_session(database, principal["principal_id"])
            self.assertTrue(validate_session(database, token))
            with MoshRepository(database) as repo:
                stored = repo.connection.execute("SELECT token_sha256 FROM identity_sessions").fetchone()[0]
            self.assertNotEqual(stored, token)
            self.assertTrue(revoke_session(database, token))
            self.assertFalse(validate_session(database, token))

    def test_verifier_errors_are_reduced_to_non_sensitive_codes(self):
        self.assertEqual(credential_failure_code(ValueError("Token used too early")), "credential_clock_skew")
        self.assertEqual(credential_failure_code(ValueError("Wrong recipient, expected audience")), "credential_invalid_audience")
        self.assertEqual(credential_failure_code(ValueError("Could not verify token signature")), "credential_signature_failed")

    def test_login_page_uses_redirect_button_without_token_logging(self):
        page = login_page("client.apps.googleusercontent.com", "http://127.0.0.1:1455/auth/callback", "csrf", "nonce").decode()
        self.assertIn('data-callback="handleCredentialResponse"', page)
        self.assertIn('data-auto_prompt="false"', page)
        self.assertIn("openid", page.lower())
        self.assertNotIn("console.log", page)
        self.assertNotIn("YOUR_GOOGLE_CLIENT_ID", page)
        self.assertIn("mosh_csrf_token:'csrf'", page)
        self.assertIn("fetch('/auth/status'", page)

    @patch("google.oauth2.id_token.verify_oauth2_token")
    def test_verification_checks_claims_after_library_signature_validation(self, mocked):
        mocked.return_value = {"iss": "https://accounts.google.com", "aud": "client", "sub": "123", "email": "a@example.com", "email_verified": True}
        claims = verify_google_credential("signed.jwt", "client")
        self.assertEqual(claims["sub"], "123")
        self.assertEqual(mocked.call_args.kwargs["clock_skew_in_seconds"], 60)
        mocked.return_value = {"iss": "https://accounts.google.com", "aud": "other", "sub": "123"}
        with self.assertRaises(ValueError):
            verify_google_credential("signed.jwt", "client")


if __name__ == "__main__":
    unittest.main()
