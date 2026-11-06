"""成员授权：作用域与角色矩阵、授权管理权限。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ..authz import has_global_permission, has_namespace_permission, has_project_permission
from ..errors import Forbidden, NotFound
from ..store import Store
from .access import project_or_404

ALLOWED_ROLES = {
    "global": {"platform_admin", "auditor"},
    "project": {"project_admin", "developer", "project_viewer"},
    "namespace": {"namespace_owner", "deployer", "namespace_viewer"},
}
BINDING_FIELDS = ("id", "subject_type", "subject_id", "role", "scope_type", "scope_id", "expires_at", "note")
REQUEST_FIELDS = {"subject_type", "subject_id", "role", "scope_type", "scope_id", "expires_at", "note"}


def binding_view(binding: Dict[str, Any]) -> Dict[str, Any]:
    return {key: binding[key] for key in BINDING_FIELDS}


def can_manage(store: Store, user: Dict[str, Any], binding: Dict[str, Any]) -> bool:
    if has_global_permission(store, user, "user.manage"):
        return True
    if binding["scope_type"] == "project":
        return has_project_permission(store, user, binding["scope_id"], "project.member.manage")
    if binding["scope_type"] == "namespace":
        return has_namespace_permission(store, user, binding["scope_id"], "namespace.member.manage")
    return False


def check_grant(store: Store, actor: Dict[str, Any], scope_type: str, scope_id: int, role: str) -> int:
    """校验 actor 能否在该范围授予该角色；返回规范化后的 scope_id。管理员角色只能由平台管理员授予。"""
    if scope_type not in ALLOWED_ROLES or role not in ALLOWED_ROLES[scope_type]:
        raise ValueError("角色与授权范围不匹配")
    platform_admin = has_global_permission(store, actor, "user.manage")
    if scope_type == "global":
        if not platform_admin:
            raise Forbidden("只有平台管理员可以授予全局角色")
        return 0
    if scope_type == "project":
        project_or_404(store, actor, scope_id, "project.member.manage")
        if role == "project_admin" and not platform_admin:
            raise Forbidden("项目成员管理员不能授予同级管理员角色")
        return scope_id
    namespace = store.get_namespace(scope_id)
    if not namespace or not has_namespace_permission(store, actor, scope_id, "namespace.member.manage"):
        raise NotFound("命名空间不存在")
    if role == "namespace_owner" and not platform_admin:
        raise Forbidden("命名空间管理员不能授予同级管理员角色")
    return scope_id


def parse_expiry(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("expires_at 必须为 ISO 8601 时间")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("expires_at 必须为 ISO 8601 时间") from None
    if parsed.tzinfo is None or parsed <= datetime.now(timezone.utc):
        raise ValueError("授权过期时间必须为未来的带时区时间")
    return value
