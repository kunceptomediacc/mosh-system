from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "mosh-core"))

from mosh_core.adapters.process import run  # noqa: E402


def classify(message: str) -> str:
    lowered = message.lower()
    categories = (
        ("timeout", ("timeout",)),
        ("authentication", ("auth", "login", "unauthorized", "forbidden")),
        ("quota", ("quota", "rate limit", "usage limit", "insufficient")),
        ("network", ("network", "connection", "dns", "resolve", "tls", "certificate")),
        ("model", ("model", "unsupported")),
        ("configuration", ("config", "invalid option", "unknown option")),
        ("sandbox", ("sandbox", "permission denied", "access denied")),
    )
    for category, markers in categories:
        if any(marker in lowered for marker in markers):
            return category
    return "none" if not message else "unclassified"


def digest(value: str) -> str | None:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Credential-safe bounded Codex exec diagnostic")
    parser.add_argument("--accounts", type=Path, default=ROOT / ".local" / "codex-accounts" / "accounts.json")
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--json-events", action="store_true")
    args = parser.parse_args()
    accounts = json.loads(args.accounts.read_text(encoding="utf-8-sig"))["accounts"]
    results = []
    for account in accounts:
        with tempfile.TemporaryDirectory(prefix="mosh-codex-probe-") as directory:
            output = Path(directory) / "result.txt"
            command = [
                "codex", "exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
                "--ignore-user-config", "--ignore-rules", "-o", str(output),
            ]
            if args.json_events:
                command.append("--json")
            command.append("Reply with exactly MOSH_CODEX_PROBE_OK. Do not use tools, inspect files, or modify anything.")
            started = time.monotonic()
            result = run(command, timeout=args.timeout, env={"CODEX_HOME": account["home"]})
            elapsed = round(time.monotonic() - started, 3)
            artifact = output.read_text(encoding="utf-8").strip() if output.is_file() else ""
            results.append({
                "account_alias": account["alias"],
                "available": result["available"],
                "exit_code": result["exit_code"],
                "elapsed_seconds": elapsed,
                "timed_out": result["stderr"] == "timeout",
                "stderr_category": classify(result["stderr"]),
                "stderr_sha256": digest(result["stderr"]),
                "stderr_length": len(result["stderr"]),
                "stdout_sha256": digest(result["stdout"]),
                "stdout_length": len(result["stdout"]),
                "artifact_present": bool(artifact),
                "artifact_matches_probe": artifact == "MOSH_CODEX_PROBE_OK",
            })
    print(json.dumps({"safe": True, "timeout_seconds": args.timeout, "json_events": args.json_events, "results": results}, indent=2))
    return 0 if all(item["artifact_matches_probe"] for item in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
