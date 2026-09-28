from __future__ import annotations


ROLE_PERMISSIONS = {
    "owner": frozenset({"dashboard.read", "task.submit", "task.execute", "approval.decide", "plugin.configure",
                        "identity.manage", "governance.manage", "artifact.review", "publish.request"}),
    "admin": frozenset({"dashboard.read", "task.submit", "task.execute", "approval.decide", "plugin.configure",
                        "governance.manage", "artifact.review"}),
    "developer": frozenset({"dashboard.read", "task.submit", "task.execute", "artifact.review"}),
    "operator": frozenset({"dashboard.read", "task.submit", "task.execute"}),
    "client": frozenset({"dashboard.read", "artifact.review"}),
    "viewer": frozenset({"dashboard.read"}),
}


def has_permission(roles: set[str] | frozenset[str], permission: str) -> bool:
    if not permission or any(role not in ROLE_PERMISSIONS for role in roles):
        return False
    return any(permission in ROLE_PERMISSIONS[role] for role in roles)


def effective_permissions(roles: set[str] | frozenset[str]) -> frozenset[str]:
    if any(role not in ROLE_PERMISSIONS for role in roles):
        return frozenset()
    return frozenset().union(*(ROLE_PERMISSIONS[role] for role in roles)) if roles else frozenset()
