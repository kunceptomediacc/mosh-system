import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "mosh-core"))

from mosh_core.n8n_adapter import N8nReadOnlyAdapter


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, _limit):
        return b'{"status":"ok"}'


class Phase7N8nAdapterTests(unittest.TestCase):
    def test_adapter_is_loopback_only_and_health_only(self):
        with self.assertRaises(ValueError):
            N8nReadOnlyAdapter("https://example.com")
        with self.assertRaises(ValueError):
            N8nReadOnlyAdapter("http://user:secret@127.0.0.1:5678")
        capabilities = N8nReadOnlyAdapter().capabilities()
        self.assertEqual(capabilities["supported"], ["health"])
        self.assertIn("workflow.execute", capabilities["unsupported"])
        self.assertFalse(capabilities["external_effects"])

    @patch("mosh_core.n8n_adapter.urlopen", return_value=_Response())
    def test_health_returns_bounded_public_result(self, mocked):
        result = N8nReadOnlyAdapter().health()
        self.assertEqual(result, {"status": "healthy", "endpoint": "loopback"})
        request = mocked.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:5678/healthz")
        self.assertEqual(request.get_method(), "GET")
