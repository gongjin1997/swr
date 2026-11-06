"""账号管理规则与当前用户的权限汇总。"""
from __future__ import annotations

from typing import Any, Dict

from ..auth import permissions_of, public_user
from ..authz import (GLOBAL_ROLE_PERMISSIONS, NAMESPACE_ROLE_PERMISSIONS, PROJECT_ROLE_PERMISSIONS,
                     visible_namespace_ids, visible_project_ids)
from ..config import Settings
from ..store import Store
from .bindings import binding_view


def protect_user(store: Store, actor: Dict[str, Any], target: Dict[str, Any], role: str, enabled: bool,
                 delete: bool = False) -> None:
    """不能删除、停用或降级自己；必须保留至少一个启用的平台管理员。"""
    if target["id"] == actor["id"] and (delete or role != target["role"] or not enabled):
        raise ValueError("不能删除、停用或更改自己的角色")
    if (store.is_platform_admin(target["id"]) and target["enabled"] and (delete or role != "admin" or not enabled)
            and not store.count_platform_admins(target["id"])):
        raise ValueError("至少保留一个启用的管理员")


def session_profile(store: Store, settings: Settings, user: Dict[str, Any]) -> Dict[str, Any]:
    """/api/auth/me：账号信息 + 合并后的有效权限 + 可见项目/命名空间及其权限。"""
    bindings = store.list_bindings(user_id=user["id"])
    effective = set(permissions_of(user["role"]) if user["role"] == "admin" else [])
    for binding in bindings:
        table = {"global": GLOBAL_ROLE_PERMISSIONS, "project": PROJECT_ROLE_PERMISSIONS}.get(
            binding["scope_type"], NAMESPACE_ROLE_PERMISSIONS)
        granted = table.get(binding["role"], set())
        effective.update(permissions_of("admin") if "*" in granted else granted)

    def scoped(scope_type: str, scope_id: int, table: Dict[str, set]) -> list:
        roles = [b["role"] for b in bindings if b["scope_type"] == scope_type and b["scope_id"] == scope_id]
        return sorted({p for role in roles for p in table.get(role, set())})

    projects = []
    for project_id in visible_project_ids(store, user):
        project = store.get_project(project_id)
        perms = scoped("project", project_id, PROJECT_ROLE_PERMISSIONS)
        effective.update(perms)
        projects.append({"id": project_id, "name": project["name"], "perms": perms})
    namespaces = []
    for namespace_id in visible_namespace_ids(store, user):
        namespace = store.get_namespace(namespace_id)
        perms = scoped("namespace", namespace_id, NAMESPACE_ROLE_PERMISSIONS)
        effective.update(perms)
        namespaces.append({"id": namespace_id, "name": namespace["name"], "cluster": namespace["cluster_name"],
                           "tier": namespace["tier"], "perms": perms})
    public = public_user(user)
    global_roles = {b["role"] for b in bindings if b["scope_type"] == "global"}
    if "platform_admin" in global_roles:
        public.update(role="platform_admin", role_label="平台管理员")
    elif "auditor" in global_roles:
        public.update(role="auditor", role_label="审计员")
    return dict(public, permissions=sorted(effective), site_name=settings.general.site_name,
                deploy_enabled=settings.general.deploy_enabled, bindings=[binding_view(b) for b in bindings],
                projects=projects, namespaces=namespaces)
