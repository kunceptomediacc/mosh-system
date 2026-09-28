import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "mosh-core"))

from mosh_core.backup import create_backup, verify_backup
from mosh_core.models import TaskEnvelope
from mosh_core.repository import MoshRepository


class BackupTests(unittest.TestCase):
    def test_backup_is_consistent_manifest_bound_and_read_only_verifiable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "live.db"
            with MoshRepository(database) as repo:
                repo.create_task(TaskEnvelope("TASK-BACKUP", "MOSH", "owner", "local", "durable"))
            created = create_backup(database, root / "backups")
            verified = verify_backup(created["backup_path"])
            self.assertTrue(verified["verified"])
            self.assertEqual(verified["sha256"], created["sha256"])
            self.assertGreaterEqual(verified["schema_version"], 17)

    def test_tampered_manifest_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "live.db"
            with MoshRepository(database):
                pass
            created = create_backup(database, root / "backups")
            manifest = Path(created["manifest_path"])
            value = json.loads(manifest.read_text())
            value["sha256"] = "0" * 64
            manifest.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                verify_backup(created["backup_path"])


if __name__ == "__main__":
    unittest.main()
