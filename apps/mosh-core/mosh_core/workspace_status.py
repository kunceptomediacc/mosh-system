from __future__ import annotations

from pathlib import Path
import os

from .adapters.process import run


ALLOWLISTED_AREAS = ("apps", "bridge", "docs", "scripts", "skills", "plugins", "tests")


def _area_counts(root: Path, name: str) -> dict:
    area = root / name
    if not area.is_dir():
        return {"name": name, "present": False, "file_count": 0, "directory_count": 0}
    files = directories = 0
    excluded = {"node_modules", "__pycache__"}
    for _, names, filenames in os.walk(area):
        names[:] = [name for name in names if not name.startswith(".") and name not in excluded]
        directories += len(names)
        files += sum(1 for name in filenames if not name.startswith("."))
    return {"name": name, "present": True, "file_count": files, "directory_count": directories}


def _git_summary(root: Path) -> dict:
    if not (root / ".git").exists():
        state = "metadata_parked" if (root / ".git-init-backup").exists() else "not_initialized"
        return {"state": state, "branch": None, "commit": None, "staged": 0, "unstaged": 0, "untracked": 0}
    branch = run(["git", "-C", str(root), "branch", "--show-current"], timeout=5)
    commit = run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], timeout=5)
    status = run(["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=normal"], timeout=10)
    if any(item["exit_code"] != 0 for item in (branch, commit, status)):
        return {"state": "unavailable", "branch": None, "commit": None, "staged": 0, "unstaged": 0, "untracked": 0}
    lines = [line for line in status["stdout"].splitlines() if len(line) >= 2]
    return {
        "state": "clean" if not lines else "dirty",
        "branch": branch["stdout"] or None,
        "commit": commit["stdout"] or None,
        "staged": sum(1 for line in lines if line[:2] != "??" and line[0] != " "),
        "unstaged": sum(1 for line in lines if line[:2] != "??" and line[1] != " "),
        "untracked": sum(1 for line in lines if line[:2] == "??"),
    }


def workspace_status(root: str | Path) -> dict:
    resolved = Path(root).resolve()
    return {
        "workspace": resolved.name,
        "areas": [_area_counts(resolved, name) for name in ALLOWLISTED_AREAS],
        "git": _git_summary(resolved),
    }
