from __future__ import annotations

from pathlib import Path
import shutil
import tempfile

from .base import ExecutionPlan, ReadOnlyAdapter, objective_reference
from .process import run


class CodexAdapter(ReadOnlyAdapter):
    name = "codex"

    def __init__(self, account_alias: str, codex_home: str | Path):
        self.account_alias = account_alias
        self.codex_home = str(Path(codex_home).resolve())

    def health(self) -> dict:
        version = run(["codex", "--version"], env={"CODEX_HOME": self.codex_home})
        login = run(["codex", "login", "status"], env={"CODEX_HOME": self.codex_home})
        login_text = login["stdout"] or login["stderr"]
        authenticated = login["exit_code"] == 0 and login_text.startswith("Logged in")
        ready = version["exit_code"] == 0 and authenticated
        return {
            "adapter": self.name,
            "healthy": ready,
            "details": {
                "account_alias": self.account_alias,
                "version": version["stdout"],
                "authenticated": authenticated,
            },
        }

    def capabilities(self) -> dict:
        result = super().capabilities()
        result["operations"]["submit"] = True
        result["operations"]["collect_result"] = True
        return result

    def execute(self, objective: str, timeout: float = 600) -> str:
        return self.execute_plan(self.build_plan(objective, timeout))

    def build_plan(self, objective: str, timeout: float = 600) -> ExecutionPlan:
        directory = tempfile.mkdtemp(prefix="mosh-codex-")
        output = Path(directory) / "result.txt"
        command = [
            "codex", "exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
            "--ignore-user-config", "--ignore-rules", "-o", str(output), objective,
        ]
        return ExecutionPlan(
            self.name, tuple(command),
            env={"CODEX_HOME": self.codex_home}, timeout=timeout, result_path=str(output),
            hashed_arg_indexes=frozenset({len(command) - 1}),
        )

    def execute_plan(self, plan: ExecutionPlan) -> str:
        output = Path(plan.result_path) if plan.result_path else None
        try:
            result = run(list(plan.argv), timeout=plan.timeout, env=plan.env)
            if result["exit_code"] != 0:
                raise RuntimeError(result["stderr"] or "Codex execution failed")
            if output is None or not output.exists():
                raise RuntimeError("Codex returned no result artifact")
            return output.read_text(encoding="utf-8").strip()
        finally:
            if output is not None:
                shutil.rmtree(output.parent, ignore_errors=True)
