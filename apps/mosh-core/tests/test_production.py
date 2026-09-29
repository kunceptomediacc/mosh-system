import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "mosh-core"))

from mosh_core.production import ProductionSettings, _security_headers


class ProductionSettingsTests(unittest.TestCase):
    def environment(self, **overrides):
        values = {
            "RENDER_EXTERNAL_URL": "https://mosh-system.onrender.com",
            "MOSH_DATABASE": "/var/data/mosh.db",
            "GOOGLE_CLIENT_ID": "client.apps.googleusercontent.com",
            "MOSH_API_TOKEN": "a" * 32,
            "MOSH_WRITE_TOKEN": "b" * 32,
            "PORT": "10000",
        }
        values.update(overrides)
        return patch.dict(os.environ, values, clear=True)

    def test_render_environment_is_validated_without_persisting_secrets(self):
        with self.environment():
            settings = ProductionSettings.from_environment()
        self.assertEqual(settings.public_origin, "https://mosh-system.onrender.com")
        self.assertEqual(str(settings.database).replace("\\", "/"), "/var/data/mosh.db")
        self.assertEqual(settings.port, 10000)

    def test_non_render_or_insecure_origins_fail_closed(self):
        for origin in ("http://mosh-system.onrender.com", "https://example.com", "https://mosh-system.onrender.com/path"):
            with self.subTest(origin=origin), self.environment(RENDER_EXTERNAL_URL=origin):
                with self.assertRaises(ValueError):
                    ProductionSettings.from_environment()

    def test_database_and_tokens_fail_closed(self):
        with self.environment(MOSH_DATABASE="/tmp/mosh.db"):
            with self.assertRaises(ValueError):
                ProductionSettings.from_environment()
        with self.environment(MOSH_API_TOKEN="short"):
            with self.assertRaises(ValueError):
                ProductionSettings.from_environment()

    def test_production_security_headers_are_strict(self):
        headers = _security_headers()
        self.assertIn("max-age=31536000", headers["Strict-Transport-Security"])
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()
