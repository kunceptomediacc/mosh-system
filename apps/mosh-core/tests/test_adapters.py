import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "mosh-core"))

from mosh_core.adapters.codex import CodexAdapter
from mosh_core.adapters.command import ClineAdapter, HermesAdapter
from mosh_core.adapters.process import run


class AdapterTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows suspended-launch isolation")
    def test_windows_job_assignment_failure_never_executes_command(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "command-started.txt"
            command = "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('started', encoding='utf-8')"

            with patch("mosh_core.adapters.process._assign_windows_job", return_value=None):
                result = run([sys.executable, "-c", command, str(marker)], timeout=1)

            self.assertEqual(
                result,
                {
                    "available": True,
                    "exit_code": None,
                    "stdout": "",
                    "stderr": "process isolation unavailable",
                },
            )
            self.assertFalse(marker.exists(), "command ran before process-tree ownership was established")

    def test_timeout_terminates_descendant_holding_captured_pipes(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "grandchild-survived.txt"
            grandchild = (
                "import pathlib,sys,time; "
                "time.sleep(1); pathlib.Path(sys.argv[1]).write_text('survived', encoding='utf-8')"
            )
            parent = (
                "import subprocess,sys,time; "
                "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]]); "
                "time.sleep(10)"
            )

            started = time.monotonic()
            result = run([sys.executable, "-c", parent, grandchild, str(marker)], timeout=0.15)
            elapsed = time.monotonic() - started

            self.assertEqual(
                result,
                {"available": True, "exit_code": None, "stdout": "", "stderr": "timeout"},
            )
            self.assertLess(elapsed, 0.75)
            time.sleep(1.0)
            self.assertFalse(marker.exists(), "timed-out grandchild was left running")

    def test_cline_command_can_be_explicitly_overridden(self):
        with patch.dict("os.environ", {"MOSH_CLINE_COMMAND": "C:\\tools\\cline.cmd"}, clear=False):
            prefix = ClineAdapter._prefix()
        self.assertIn("C:\\tools\\cline.ps1", prefix)

    @patch("mosh_core.adapters.command.run")
    def test_cline_acceptance_keeps_submission_disabled_and_approval_off(self, mocked_run):
        mocked_run.return_value = {"exit_code": 0, "stdout": "[]", "stderr": "", "available": True}
        result = ClineAdapter().acceptance_probe(ROOT / ".local" / "test-acceptance")
        self.assertFalse(result["submit_enabled"])
        self.assertEqual(result["required_submit_flags"][0:2], ["--auto-approve", "false"])
        command = mocked_run.call_args_list[0].args[0]
        self.assertIn("--config", command)

    @patch("mosh_core.adapters.command.run")
    def test_account_bound_cline_executes_with_approval_disabled(self, mocked_run):
        mocked_run.return_value = {
            "exit_code": 0, "stderr": "", "available": True,
            "stdout": '{"type":"run_result","finishReason":"completed","text":"MOSH_CLINE_OK"}',
        }
        adapter = ClineAdapter("primary", ROOT / ".local" / "adapter-acceptance-v1")
        self.assertTrue(adapter.capabilities()["operations"]["submit"])
        self.assertEqual(adapter.execute("probe"), "MOSH_CLINE_OK")
        command = mocked_run.call_args.args[0]
        self.assertIn("false", command)
        self.assertNotIn("--yolo", command)

    @patch("mosh_core.adapters.command.run")
    def test_cline_accepts_current_agent_event_result(self, mocked_run):
        mocked_run.return_value = {
            "exit_code": 0, "stderr": "", "available": True,
            "stdout": '{"type":"agent_event","event":{"type":"done","reason":"completed","text":"CURRENT_OK"}}',
        }
        adapter = ClineAdapter("primary", ROOT / ".local" / "adapter-acceptance-v1")
        self.assertEqual(adapter.execute("probe"), "CURRENT_OK")

    @patch("mosh_core.adapters.command.run")
    def test_hermes_acceptance_isolates_home_and_forbids_bypass_modes(self, mocked_run):
        mocked_run.return_value = {"exit_code": 0, "stdout": "No sessions found.", "stderr": "", "available": True}
        result = HermesAdapter().acceptance_probe(ROOT / ".local" / "test-acceptance")
        self.assertFalse(result["submit_enabled"])
        self.assertEqual(result["forbidden_submit_modes"], ["--yolo"])
        for call in mocked_run.call_args_list:
            self.assertTrue(call.kwargs["env"]["HERMES_HOME"].endswith("hermes"))

    @patch("mosh_core.adapters.command.run")
    def test_account_bound_hermes_uses_safe_stream_protocol(self, mocked_run):
        mocked_run.return_value = {
            "exit_code": 0, "stderr": "", "available": True,
            "stdout": '{"type":"result","session_id":"S1","exit_code":0,"text":"MOSH_HERMES_OK"}',
        }
        adapter = HermesAdapter("primary", ROOT / ".local" / "adapter-acceptance-v1")
        self.assertTrue(adapter.capabilities()["operations"]["submit"])
        self.assertEqual(adapter.execute("probe"), "MOSH_HERMES_OK")
        command = mocked_run.call_args.args[0]
        self.assertIn("--safe-mode", command)
        self.assertIn("--source", command)
        self.assertEqual(command[command.index("--source") + 1], "tool")
        self.assertNotIn("--oneshot", command)
        self.assertNotIn("--yolo", command)

    @patch("mosh_core.adapters.codex.run")
    def test_codex_account_context_is_explicit(self, mocked_run):
        mocked_run.side_effect = [
            {"exit_code": 0, "stdout": "codex-cli 1", "stderr": "", "available": True},
            {"exit_code": 0, "stdout": "Logged in using ChatGPT", "stderr": "", "available": True},
        ]
        adapter = CodexAdapter("work", ROOT / ".local" / "codex-accounts" / "work")
        result = adapter.capabilities()
        self.assertTrue(result["healthy"])
        self.assertEqual(result["details"]["account_alias"], "work")
        for call in mocked_run.call_args_list:
            self.assertTrue(call.kwargs["env"]["CODEX_HOME"].endswith("work"))
        self.assertTrue(result["operations"]["submit"])
        self.assertTrue(result["operations"]["collect_result"])

    @patch("mosh_core.adapters.codex.run")
    def test_codex_accepts_login_status_on_stderr(self, mocked_run):
        mocked_run.side_effect = [
            {"exit_code": 0, "stdout": "codex-cli 1", "stderr": "", "available": True},
            {"exit_code": 0, "stdout": "", "stderr": "Logged in using ChatGPT", "available": True},
        ]
        result = CodexAdapter("personal", ROOT / ".local" / "codex-accounts" / "personal").health()
        self.assertTrue(result["healthy"])
        self.assertTrue(result["details"]["authenticated"])


if __name__ == "__main__":
    unittest.main()
