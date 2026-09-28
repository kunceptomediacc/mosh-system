import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "mosh-core"))

from mosh_core.repository import MoshRepository


class Phase9VideoFactoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = MoshRepository(Path(self.temp.name) / "video.db")
        self.repo.configure_project_governance("VIDEO", "experiment", "internal", "video-zone-2026", "owner", True)

    def tearDown(self):
        self.repo.close()
        self.temp.cleanup()

    @staticmethod
    def digest(value):
        return hashlib.sha256(value.encode()).hexdigest()

    def test_video_pipeline_is_ordered_digest_bound_and_publish_separate(self):
        video = self.repo.create_video_project("VIDEO", self.digest("brief"), "codex")
        with self.assertRaises(ValueError):
            self.repo.complete_video_stage(video["video_id"], "story", self.digest("story"), "codex")
        for stage in ("inspiration", "story", "scene_plan", "media", "narration", "composition", "render", "review"):
            record = self.repo.complete_video_stage(video["video_id"], stage, self.digest(stage), "codex")
            self.assertEqual(record["status"], "completed")
        with self.assertRaises(ValueError):
            self.repo.complete_video_stage(video["video_id"], "publish", self.digest("publish"), "codex")
        approved = self.repo.approve_video_project(video["video_id"], self.digest("owner review"), "owner")
        self.assertEqual(approved["status"], "approved")
        publish = self.repo.connection.execute(
            "SELECT * FROM video_stage_records WHERE video_id=? AND stage='publish'", (video["video_id"],)
        ).fetchone()
        self.assertEqual(publish["status"], "pending")

    def test_public_video_status_is_aggregate(self):
        video = self.repo.create_video_project("VIDEO", self.digest("private brief"), "codex")
        status = self.repo.video_status_public()
        self.assertEqual(status["projects"]["draft"], 1)
        self.assertEqual(status["stages"]["pending"], 9)
        self.assertEqual(status["publishing"], "approval_gated_unavailable")
        encoded = json.dumps(status)
        self.assertNotIn(video["video_id"], encoded)
        self.assertNotIn(self.digest("private brief"), encoded)


if __name__ == "__main__":
    unittest.main()
