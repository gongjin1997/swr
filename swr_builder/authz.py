"""Scoped authorization helpers shared by HTTP routes and background workers."""
from typing import Any, Dict, List

from .store import Store

PROJECT_ROLE_PERMISSIONS = {
    "platform_admin": {"*"},
    "auditor": {"audit.view", "project.view", "app.view", "job.view", "deploy.view", "namespace.view"},
    "project_admin": {"project.view", "project.member.manage", "app.view", "app.manage", "build.run", "job.view", "job.cancel.own", "job.cancel"},
    "developer": {"project.view", "app.view", "app.manage", "build.run", "job.view", "job.cancel.own"},
    "project_viewer": {"project.view", "app.view", "job.view"},
}

NAMESPACE_ROLE_PERMISSIONS = {
    "namespace_approver": {"namespace.view", "deploy.view", "deploy.approve"},
    "namespace_owner": {"namespace.view", "namespace.member.manage", "service.config", "service.deploy",
        "service.operate", "service.rollback", "service.delete", "secret.manage", "pod.log.view"},
    "deployer": {"namespace.view", "service.config", "service.deploy", "service.operate", "pod.log.view"},
    "namespace_viewer": {"namespace.view", "pod.log.view"},
}

GLOBAL_ROLE_PERMISSIONS = {
    "platform_admin": {"*"},
    "auditor": {"audit.view", "project.view", "app.view", "job.view", "deploy.view", "namespace.view"},
    "deployment_approver": {"deploy.approve", "deploy.view", "namespace.view"},
}


def role_permissions(store: Store, scope_type: str) -> Dict[str, set]:
    """Resolve built-in and enabled custom roles, including global grants."""
    table = dict(GLOBAL_ROLE_PERMISSIONS)
    table.update({"project": PROJECT_ROLE_PERMISSIONS, "namespace": NAMESPACE_ROLE_PERMISSIONS}.get(scope_type, {}))
    for role in store.list_custom_roles():
        if role["enabled"] and role["scope_type"] in {"global", scope_type}:
            table[role["code"]] = set(role["permissions"])
    return table


def has_global_permission(store: Store, user: Dict[str, Any], permission: str) -> bool:
    current=store.get_user(user['id'])
    if current and current['role']=='admin':return True
    return store.user_has_scope_permission(user['id'],role_permissions(store, 'global'),permission,'global',0)


def has_project_permission(store: Store, user: Dict[str, Any], project_id: int, permission: str) -> bool:
    current = store.get_user(user["id"])
    if current and current["role"] == "admin":
        return True
    return store.user_has_scope_permission(user["id"], role_permissions(store, "project"), permission, "project", project_id)


def visible_project_ids(store: Store, user: Dict[str, Any], permission: str = "project.view") -> List[int]:
    current = store.get_user(user["id"])
    if current and current["role"] == "admin":
        return [p["id"] for p in store.list_projects()]
    return store.project_ids_for_user(user["id"], role_permissions(store, "project"), permission)


def has_namespace_permission(store: Store, user: Dict[str, Any], namespace_id: int, permission: str) -> bool:
    current=store.get_user(user['id'])
    if current and current['role']=='admin':return True
    return store.user_has_scope_permission(user['id'],role_permissions(store, 'namespace'),permission,'namespace',namespace_id)


def visible_namespace_ids(store: Store, user: Dict[str, Any], permission: str = "namespace.view") -> List[int]:
    current=store.get_user(user['id'])
    if current and current['role']=='admin':return [n['id'] for n in store.list_namespaces()]
    bindings=store.list_bindings(user_id=user['id'])
    table = role_permissions(store, 'namespace')
    if any(b['scope_type']=='global' and (permission in table.get(b['role'],set()) or
           '*' in table.get(b['role'],set())) for b in bindings):
        return [n['id'] for n in store.list_namespaces()]
    ids=set(store.namespace_ids_for_user(user['id'],table,permission))
    return sorted(ids)
