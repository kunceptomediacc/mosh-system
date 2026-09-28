from __future__ import annotations

from collections import Counter
from pathlib import Path


INTEGRATION_AREAS = (
    ("plugins_installed", "plugins/installed"),
    ("plugins_registry", "plugins/registry"),
    ("mcp_servers", "tools/mcp"),
    ("local_tools", "tools/local"),
)


def _entry_count(root: Path, kind: str, relative: str) -> dict:
    area = root.joinpath(*relative.split("/"))
    if not area.is_dir():
        return {"kind": kind, "present": False, "entry_count": 0}
    count = sum(1 for item in area.iterdir() if not item.name.startswith("."))
    return {"kind": kind, "present": True, "entry_count": count}


def integration_status(root: str | Path, agents: list[dict]) -> dict:
    resolved = Path(root).resolve()
    capability_counts = Counter(
        capability
        for agent in agents
        for capability in agent.get("capabilities", [])
        if isinstance(capability, str) and capability
    )
    return {
        "agents": {
            "registered": len(agents),
            "ready": sum(1 for agent in agents if agent.get("status") == "ready"),
        },
        "capabilities": [
            {"name": name, "agent_count": count}
            for name, count in sorted(capability_counts.items())
        ],
        "areas": [_entry_count(resolved, kind, relative) for kind, relative in INTEGRATION_AREAS],
    }
