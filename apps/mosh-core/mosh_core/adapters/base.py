from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any


READ_ONLY_OPERATIONS = {
    "health": True,
    "capabilities": True,
    "submit": False,
    "status": False,
    "stream": False,
    "cancel": False,
    "collect_result": False,
}


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """An executable command paired with the only safe representation we persist."""

    adapter_name: str
    argv: tuple[str, ...]
    env: dict[str, str] = field(default_factory=dict)
    timeout: float = 600
    result_path: str | None = None
    hashed_arg_indexes: frozenset[int] = frozenset()
    sensitive_arg_indexes: frozenset[int] = frozenset()

    def __post_init__(self) -> None:
        if not self.adapter_name or not self.argv:
            raise ValueError("execution plan requires an adapter and argv")
        indexes = self.hashed_arg_indexes | self.sensitive_arg_indexes
        if any(index < 0 or index >= len(self.argv) for index in indexes):
            raise ValueError("sensitive argv index is out of range")

    def display_argv(self) -> tuple[str, ...]:
        safe_executables = {"codex", "codex.exe", "cline", "cline.cmd", "hermes",
                            "powershell.exe", "pwsh.exe"}
        safe_tokens = {
            "exec", "chat", "--ephemeral", "--skip-git-repo-check", "--sandbox",
            "read-only", "--ignore-user-config", "--ignore-rules", "-o",
            "-noprofile", "-executionpolicy", "bypass", "-file", "--json",
            "--auto-approve", "false", "--config", "--data-dir", "--cwd", "--timeout",
            "--safe-mode", "--format", "stream-json", "--provider", "openai-codex",
            "--source", "tool", "--max-turns", "--run-budget", "-q",
        }
        result: list[str] = []
        for index, argument in enumerate(self.argv):
            if index in self.hashed_arg_indexes:
                result.append(objective_reference(argument))
            elif index in self.sensitive_arg_indexes:
                result.append("<redacted>")
            elif index == 0 and Path(argument).name.lower() in safe_executables:
                result.append(Path(argument).name)
            elif argument.lower() in safe_tokens:
                result.append(argument)
            else:
                # Default deny: paths, values, unknown flags, and inline flag values never persist.
                result.append("<redacted>")
        return tuple(result)

    def evidence(self) -> tuple[str, str, str]:
        display_argv = self.display_argv()
        argv_json = json.dumps(list(display_argv), ensure_ascii=False, separators=(",", ":"))
        env_keys_json = json.dumps(sorted(self.env), ensure_ascii=False, separators=(",", ":"))
        canonical = json.dumps(
            {"adapter": self.adapter_name, "argv": list(display_argv), "env_keys": sorted(self.env)},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        return argv_json, env_keys_json, hashlib.sha256(canonical).hexdigest()


def objective_reference(objective: str) -> str:
    digest = hashlib.sha256(objective.encode("utf-8")).hexdigest()
    return f"<objective:sha256:{digest}>"


class ReadOnlyAdapter(ABC):
    name: str

    @abstractmethod
    def health(self) -> dict[str, Any]: ...

    def capabilities(self) -> dict[str, Any]:
        result = self.health()
        return {
            "adapter": self.name,
            "healthy": bool(result["healthy"]),
            "operations": dict(READ_ONLY_OPERATIONS),
            "details": result["details"],
        }
