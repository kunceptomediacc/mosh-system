from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

from .base import ExecutionPlan, ReadOnlyAdapter, objective_reference
from .process import run


class ClineAdapter(ReadOnlyAdapter):
    name = "cline"

    def __init__(self, account_alias: str | None = None, state_root: str | Path | None = None):
        self.account_alias = account_alias
        self.state_root = Path(state_root).resolve() if state_root else None

    @staticmethod
    def _prefix() -> list[str]:
        if os.name == "nt":
            override = os.environ.get("MOSH_CLINE_COMMAND")
            local_shim = Path.cwd() / ".local" / "cline-cli" / "node_modules" / ".bin" / "cline.cmd"
            command_shim = override or (str(local_shim) if local_shim.is_file() else shutil.which("cline.cmd"))
            script = str(command_shim[:-4] + ".ps1") if command_shim and command_shim.lower().endswith(".cmd") else None
            powershell = shutil.which("powershell.exe")
            return [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script] if script and powershell else ["cline.cmd"]
        return ["cline"]

    def health(self) -> dict:
        prefix = self._prefix()
        version = run(prefix + ["--version"], timeout=30)
        hub = run(prefix + ["hub", "status"], timeout=30)
        try:
            hub_status = json.loads(hub["stdout"]) if hub["stdout"] else {}
        except json.JSONDecodeError:
            hub_status = {}
        return {
            "adapter": self.name,
            "healthy": version["exit_code"] == 0,
            "details": {
                "version": version["stdout"],
                "hub_running": bool(hub_status.get("running", False)),
                "core_version": hub_status.get("coreVersion") or hub_status.get("cliVersion"),
            },
        }

    def capabilities(self) -> dict:
        result = super().capabilities()
        if self.account_alias and self.state_root:
            result["operations"]["submit"] = True
            result["operations"]["collect_result"] = True
            result["details"]["account_alias"] = self.account_alias
        return result

    def execute(self, objective: str, timeout: float = 600) -> str:
        return self.execute_plan(self.build_plan(objective, timeout))

    def build_plan(self, objective: str, timeout: float = 600) -> ExecutionPlan:
        if not self.account_alias or not self.state_root:
            raise RuntimeError("Cline execution requires an account-bound isolated state")
        config = self.state_root / "cline"
        command = self._prefix() + [
            "--json", "--auto-approve", "false", "--config", str(config),
            "--data-dir", str(config / "data"), "--cwd", str(Path.cwd()),
            "--timeout", str(int(timeout)), objective,
        ]
        return ExecutionPlan(
            self.name, tuple(command), timeout=timeout + 15,
            hashed_arg_indexes=frozenset({len(command) - 1}),
        )

    def execute_plan(self, plan: ExecutionPlan) -> str:
        result = run(list(plan.argv), timeout=plan.timeout, env=plan.env or None)
        if result["exit_code"] != 0:
            raise RuntimeError(result["stderr"] or "Cline execution failed")
        final = None
        for line in result["stdout"].splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "run_result" and event.get("finishReason") == "completed":
                final = event.get("text")
            nested = event.get("event") if event.get("type") == "agent_event" else None
            if isinstance(nested, dict) and nested.get("type") == "done" and nested.get("reason") == "completed":
                final = nested.get("text")
        if not isinstance(final, str):
            raise RuntimeError("Cline returned no completed result")
        return final.strip()

    def acceptance_probe(self, state_root: str | Path) -> dict:
        config = Path(state_root).resolve() / "cline"
        command = self._prefix() + ["history", "--json", "--limit", "1", "--config", str(config)]
        first = run(command, timeout=30)
        second = run(command, timeout=30)
        database = config / "data" / "db" / "sessions.db"
        return {
            "adapter": self.name,
            "isolated_state": first["exit_code"] == 0 and second["exit_code"] == 0 and database.exists(),
            "restart_readable": second["exit_code"] == 0,
            "state_path": str(config),
            "approval_default": "auto-approve",
            "required_submit_flags": ["--auto-approve", "false", "--json", "--config", str(config)],
            "account_bound": False,
            "submit_enabled": False,
            "blocker": "isolated acceptance state has no configured provider credential",
        }


class HermesAdapter(ReadOnlyAdapter):
    name = "hermes"

    def __init__(self, account_alias: str | None = None, state_root: str | Path | None = None):
        self.account_alias = account_alias
        self.state_root = Path(state_root).resolve() if state_root else None

    def health(self) -> dict:
        version = run(["hermes", "--version"], timeout=20)
        gateway = run(["hermes", "gateway", "status"], timeout=20)
        return {
            "adapter": self.name,
            "healthy": version["exit_code"] == 0,
            "details": {
                "version": version["stdout"].splitlines()[0] if version["stdout"] else "",
                "gateway_running": "Gateway is running" in gateway["stdout"],
                "gateway_status_readable": gateway["exit_code"] is not None,
                "warning": gateway["stderr"][:500] if gateway["stderr"] else None,
            },
        }

    def capabilities(self) -> dict:
        result = super().capabilities()
        if self.account_alias and self.state_root:
            result["operations"]["submit"] = True
            result["operations"]["collect_result"] = True
            result["details"]["account_alias"] = self.account_alias
        return result

    def execute(self, objective: str, timeout: float = 600) -> str:
        return self.execute_plan(self.build_plan(objective, timeout))

    def build_plan(self, objective: str, timeout: float = 600) -> ExecutionPlan:
        if not self.account_alias or not self.state_root:
            raise RuntimeError("Hermes execution requires an account-bound isolated state")
        home = self.state_root / "hermes"
        command = [
            "hermes", "chat", "--safe-mode", "--format", "stream-json",
            "--provider", "openai-codex", "--source", "tool", "--max-turns", "1",
            "--run-budget", str(int(timeout)), "-q", objective,
        ]
        return ExecutionPlan(
            self.name, tuple(command), env={"HERMES_HOME": str(home)}, timeout=timeout + 15,
            hashed_arg_indexes=frozenset({len(command) - 1}),
        )

    def execute_plan(self, plan: ExecutionPlan) -> str:
        result = run(list(plan.argv), timeout=plan.timeout, env=plan.env)
        if result["exit_code"] != 0:
            raise RuntimeError(result["stderr"] or "Hermes execution failed")
        final = None
        for line in result["stdout"].splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "result" and event.get("exit_code") == 0:
                final = event.get("text")
        if not isinstance(final, str):
            raise RuntimeError("Hermes returned no completed result")
        return final.strip()

    def acceptance_probe(self, state_root: str | Path) -> dict:
        home = Path(state_root).resolve() / "hermes"
        env = {"HERMES_HOME": str(home)}
        command = ["hermes", "sessions", "list", "--limit", "1"]
        first = run(command, timeout=30, env=env)
        second = run(command, timeout=30, env=env)
        return {
            "adapter": self.name,
            "isolated_state": first["exit_code"] == 0 and second["exit_code"] == 0 and (home / "sessions").is_dir(),
            "restart_readable": second["exit_code"] == 0,
            "state_path": str(home),
            "approval_default": "interactive",
            "forbidden_submit_modes": ["--yolo"],
            "required_submit_flags": ["chat", "--safe-mode", "--format", "stream-json", "--source", "tool"],
            "account_bound": False,
            "submit_enabled": False,
            "blocker": "isolated acceptance state has no configured provider credential",
        }
