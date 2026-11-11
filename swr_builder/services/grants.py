"""成员授权：谁能在什么范围授予或撤销哪个角色。

规则（授予与撤销对称：能授予的才能撤销）：
1. 全局角色只能由平台管理员授予或撤销。
2. 项目 / 命名空间角色需要在该范围拥有成员管理权限（project.member.manage / namespace.member.manage）。
3. 非平台管理员只能授予权限不超过自己在该范围权限的角色，且不能授予带成员管理权限的角色（避免授权链扩散）。
4. 平台管理员授权的撤销还要求：不是撤销自己，且撤销后仍有至少一名启用的平台管理员。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from ..access import (GLOBAL, MEMBER_MANAGE_PERMISSIONS, NAMESPACE, PLATFORM_ADMIN_ROLE, PROJECT, SCOPE_LABELS,
                      SCOPE_TYPES, Principal, RoleDef, role_index)
from ..errors import DomainError, Forbidden, NotFound
from ..store import Store, utcnow
from ..validation import reject_unknown, text_field

REQUEST_FIELDS = {"subject_type", "subject_id", "role", "scope_type", "scope_id", "expires_at", "note"}
BINDING_FIELDS = ("id", "subject_type", "subject_id", "role", "scope_type", "scope_id", "expires_at", "note")


def binding_view(binding: Dict[str, Any]) -> Dict[str, Any]:
    return {key: binding[key] for key in BINDING_FIELDS}


def is_platform_admin_binding(binding: Mapping[str, Any]) -> bool:
    return binding["role"] == PLATFORM_ADMIN_ROLE and binding["scope_type"] == GLOBAL


def _assign_denied(me: Principal, role: RoleDef, scope_type: str, scope_id: int) -> Optional[str]:
    """me 能否在该范围授予（或撤销）role；返回拒绝原因，None 表示可以。"""
    if me.is_platform_admin:
        return None
    if scope_type == GLOBAL:
        return "只有平台管理员可以授予或撤销全局角色"
    if not me.can_in(scope_type, scope_id, MEMBER_MANAGE_PERMISSIONS[scope_type]):
        return "没有该范围的成员管理权限"
    if role.effective & set(MEMBER_MANAGE_PERMISSIONS.values()):
        return "只有平台管理员可以授予带成员管理权限的角色"
    if not role.effective <= me.permissions_in(scope_type, scope_id):
        return "不能授予超出自身权限的角色"
    return None


def check_grant(store: Store, me: Principal, scope_type: Any, scope_id: Any, role_code: Any,
                roles: Optional[Mapping[str, RoleDef]] = None) -> int:
    """校验 me 能否在该范围授予该角色；返回规范化后的 scope_id。无权管理的资源按不存在处理。"""
    if scope_type not in SCOPE_TYPES:
        raise ValueError("授权范围无效")
    if scope_type == GLOBAL:
        scope_id = 0
    elif type(scope_id) is not int:
        raise ValueError("scope_id 格式错误")
    role = (roles if roles is not None else role_index(store)).get(role_code) if isinstance(role_code, str) else None
    if role is None or not role.enabled or role.scope != scope_type:
        raise ValueError("角色与授权范围不匹配")
    if scope_type == PROJECT and (not store.get_project(scope_id)
                                  or not me.can_project(scope_id, MEMBER_MANAGE_PERMISSIONS[PROJECT])):
        raise NotFound("项目不存在")
    if scope_type == NAMESPACE and (not store.get_namespace(scope_id)
                                    or not me.can_namespace(scope_id, MEMBER_MANAGE_PERMISSIONS[NAMESPACE])):
        raise NotFound("命名空间不存在")
    reason = _assign_denied(me, role, scope_type, scope_id)
    if reason:
        raise Forbidden(reason)
    return scope_id


def can_revoke(me: Principal, binding: Mapping[str, Any], roles: Mapping[str, RoleDef]) -> bool:
    if me.is_platform_admin:
        return True
    role = roles.get(binding["role"])
    return role is not None and _assign_denied(me, role, binding["scope_type"], binding["scope_id"]) is None


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
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def normalize_grant(store: Store, me: Principal, data: Any, roles: Mapping[str, RoleDef]) -> Dict[str, Any]:
    """校验单条授权（不含授权对象），返回可写入 store 的字段。"""
    if not isinstance(data, dict):
        raise ValueError("授权格式错误")
    scope_type, role = data.get("scope_type"), data.get("role")
    scope_id = check_grant(store, me, scope_type, data.get("scope_id", 0), role, roles)
    return {"role": role, "scope_type": scope_type, "scope_id": scope_id,
            "expires_at": parse_expiry(data.get("expires_at")), "note": text_field(data, "note", 200)}


def grant(store: Store, me: Principal, data: Any) -> Dict[str, Any]:
    reject_unknown(data, REQUEST_FIELDS, "授权请求包含未知字段")
    if data.get("subject_type") != "user":
        raise ValueError("当前只支持直接绑定用户")
    subject_id = data.get("subject_id")
    if type(subject_id) is not int or not store.get_user(subject_id):
        raise ValueError("用户不存在")
    values = normalize_grant(store, me, data, role_index(store))
    binding_id = store.create_binding("user", subject_id, values["role"], values["scope_type"], values["scope_id"],
                                      me.id, values["expires_at"], values["note"])
    return store.get_binding(binding_id)  # type: ignore[return-value]


def revoke(store: Store, me: Principal, binding_id: int) -> Dict[str, Any]:
    binding = store.get_binding(binding_id)
    if not binding or not can_revoke(me, binding, role_index(store)):
        raise NotFound("授权不存在")
    if is_platform_admin_binding(binding):
        if binding["subject_id"] == me.id:
            raise DomainError("不能撤销自己的平台管理员授权，请由其他平台管理员操作")
        if not store.count_platform_admins(exclude_id=binding["subject_id"]):
            raise DomainError("至少保留一名启用的平台管理员")
    store.delete_binding(binding_id)
    return binding


def describe(binding: Mapping[str, Any], roles: Optional[Mapping[str, RoleDef]] = None) -> str:
    """审计用的授权摘要，如 project:3 role=developer user=7。"""
    role = (roles or {}).get(binding["role"])
    name = f"({role.name})" if role else ""
    return (f"user={binding['subject_id']} role={binding['role']}{name} "
            f"scope={binding['scope_type']}:{binding['scope_id']}")


# ------------------------------------------------------------------ 列表与选项
def _scope_label(row: Mapping[str, Any]) -> str:
    if row["scope_type"] == PROJECT:
        return row.get("project_name") or f"项目 #{row['scope_id']}"
    if row["scope_type"] == NAMESPACE:
        if row.get("namespace_name"):
            return f"{row.get('cluster_name') or '-'} / {row['namespace_name']}"
        return f"命名空间 #{row['scope_id']}"
    return SCOPE_LABELS[GLOBAL]


def binding_row_view(row: Mapping[str, Any], me: Principal, roles: Mapping[str, RoleDef], now: str) -> Dict[str, Any]:
    role = roles.get(row["role"])
    return dict(
        binding_view(dict(row)),
        created_at=row["created_at"], created_by=row["created_by"], created_by_username=row.get("created_by_username"),
        role_name=role.name if role else row["role"], role_enabled=bool(role and role.enabled),
        scope_label=_scope_label(row), status="expired" if row["expires_at"] and row["expires_at"] <= now else "active",
        subject={"id": row["subject_id"], "username": row.get("subject_username") or f"#{row['subject_id']}",
                 "display_name": row.get("subject_display_name") or row.get("subject_username") or "",
                 "enabled": bool(row.get("subject_enabled"))},
        can_revoke=can_revoke(me, row, roles))


def visible_binding_rows(store: Store, me: Principal, *, user_id: Optional[int] = None,
                         scope_type: Optional[str] = None, scope_id: Optional[int] = None,
                         role: Optional[str] = None) -> List[Dict[str, Any]]:
    """平台管理员与账号管理员看到全部授权；项目/命名空间成员管理员只看到自己管理范围内的授权。"""
    rows = store.list_binding_rows(user_id, scope_type, scope_id, role)
    if not (me.is_platform_admin or me.can("user.manage")):
        rows = [r for r in rows if r["scope_type"] in MEMBER_MANAGE_PERMISSIONS
                and me.can_in(r["scope_type"], r["scope_id"], MEMBER_MANAGE_PERMISSIONS[r["scope_type"]])]
    roles, now = role_index(store), utcnow()
    return [binding_row_view(row, me, roles, now) for row in rows]


def can_manage_members(me: Principal) -> bool:
    return (me.is_platform_admin or me.can("user.manage")
            or any(me.can_anywhere(p) for p in MEMBER_MANAGE_PERMISSIONS.values()))


def grant_options(store: Store, me: Principal) -> Dict[str, Any]:
    """当前账号可以授予的范围、资源与角色，前端据此只展示有效选项。"""
    roles = [r for r in role_index(store).values() if r.enabled]

    def assignable(scope_type: str, scope_id: int) -> List[str]:
        return [r.code for r in roles if r.scope == scope_type and _assign_denied(me, r, scope_type, scope_id) is None]

    result: Dict[str, Any] = {"global": None, "project": [], "namespace": []}
    if me.is_platform_admin:
        result["global"] = {"roles": assignable(GLOBAL, 0)}
    project_ids = me.project_ids(MEMBER_MANAGE_PERMISSIONS[PROJECT])
    for project in store.list_projects():
        if project_ids is None or project["id"] in project_ids:
            codes = assignable(PROJECT, project["id"])
            if codes:
                result["project"].append({"id": project["id"], "name": project["name"], "slug": project["slug"],
                                          "roles": codes})
    namespace_ids = me.namespace_ids(MEMBER_MANAGE_PERMISSIONS[NAMESPACE])
    if namespace_ids is None or namespace_ids:
        for namespace in store.list_namespaces(namespace_ids):
            codes = assignable(NAMESPACE, namespace["id"])
            if codes:
                result["namespace"].append({"id": namespace["id"], "name": namespace["name"],
                                            "cluster": namespace["cluster_name"], "tier": namespace["tier"],
                                            "roles": codes})
    return result
