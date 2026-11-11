"""账号管理规则、账号展示与当前用户的权限汇总（/api/auth/me）。

管理规则：
1. 平台管理员可以管理所有账号；只有平台管理员能创建平台管理员，或管理（含停用中的）平台管理员账号。
2. 其他持有 user.manage 的账号管理员只能管理"全局权限不超过自己"的账号，避免通过重置密码接管更高权限账号。
3. 不能删除、停用自己，也不能修改自己的平台管理员身份；任何时候至少保留一名启用的平台管理员。
4. 初始授权与"授权"接口使用同一套规则（services/grants.py）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple

from ..access import (GLOBAL, NAMESPACE, PROJECT, Principal, RoleDef, load_principal, principals_for, role_index)
from ..auth import USERNAME_RE, AuthService
from ..config import Settings
from ..errors import Conflict, Forbidden, NotFound
from ..security import hash_password
from ..store import Store
from ..validation import reject_unknown, text_field
from . import grants as grant_rules

CREATE_FIELDS = {"username", "display_name", "email", "password", "platform_admin", "grants", "role"}
UPDATE_FIELDS = {"display_name", "email", "enabled", "platform_admin", "role"}
# 兼容 v1.3 接口的 role 参数：admin → 平台管理员；developer / viewer → 默认项目的对应角色（仅创建时）
LEGACY_ROLES = {"admin": None, "developer": "developer", "viewer": "project_viewer"}
MAX_INITIAL_GRANTS = 20


# ------------------------------------------------------------------ 展示
def role_summary(principal: Principal, roles: Mapping[str, RoleDef]) -> Tuple[str, str]:
    """(role, role_label)：账号菜单和账号列表里的简短身份说明。"""
    if principal.is_platform_admin:
        return "platform_admin", "平台管理员"
    global_roles = sorted({b["role"] for b in principal.bindings if b["scope_type"] == GLOBAL})
    if global_roles:
        return global_roles[0], "、".join(roles[c].name if c in roles else c for c in global_roles)
    scoped = []
    for binding in principal.bindings:
        name = roles[binding["role"]].name if binding["role"] in roles else binding["role"]
        if name not in scoped:
            scoped.append(name)
    if scoped:
        return "member", "、".join(scoped[:2]) + ("等" if len(scoped) > 2 else "")
    return "member", "未授权"


def account_view(user: Dict[str, Any], principal: Principal, roles: Mapping[str, RoleDef]) -> Dict[str, Any]:
    """返回给前端的账号信息（不含密码哈希）。principal 应按 include_disabled=True 计算，以展示停用账号持有的授权。"""
    role, label = role_summary(principal, roles)
    return {
        "id": user["id"], "username": user["username"], "display_name": user.get("display_name") or user["username"],
        "email": user.get("email") or "", "role": role, "role_label": label,
        "is_platform_admin": principal.is_platform_admin,
        "global_roles": sorted({b["role"] for b in principal.bindings if b["scope_type"] == GLOBAL}),
        "grant_count": len(principal.bindings),
        "enabled": bool(user["enabled"]), "must_change_password": bool(user["must_change_password"]),
        "created_at": user.get("created_at"), "last_login_at": user.get("last_login_at"),
        "password_changed_at": user.get("password_changed_at"),
    }


def view_of(store: Store, user: Dict[str, Any]) -> Dict[str, Any]:
    roles = role_index(store)
    return account_view(user, load_principal(store, user, include_disabled=True, roles=roles), roles)


def manage_denied(me: Principal, target: Principal) -> Optional[str]:
    """me 能否管理 target 账号（target 按 include_disabled=True 计算）；返回拒绝原因，None 表示可以。"""
    if me.is_platform_admin:
        return None
    if not me.can("user.manage"):
        return "没有账号管理权限"
    if target.is_platform_admin:
        return "只有平台管理员可以管理平台管理员账号"
    if not target.global_perms <= me.global_perms:
        return "不能管理全局权限高于自己的账号"
    return None


def list_accounts(store: Store, me: Principal) -> List[Dict[str, Any]]:
    users = store.list_users()
    roles = role_index(store)
    principals = principals_for(store, users)
    return [dict(account_view(u, principals[u["id"]], roles), can_manage=manage_denied(me, principals[u["id"]]) is None)
            for u in users]


def _target(store: Store, me: Principal, user_id: int) -> Tuple[Dict[str, Any], Principal]:
    user = store.get_user(user_id)
    if not user:
        raise NotFound("账号不存在")
    target = load_principal(store, user, include_disabled=True)
    reason = manage_denied(me, target)
    if reason:
        raise Forbidden(reason)
    return user, target


def account_detail(store: Store, me: Principal, user_id: int) -> Dict[str, Any]:
    """账号详情：基本信息、授权记录与按范围汇总的有效权限。"""
    user = store.get_user(user_id)
    if not user:
        raise NotFound("账号不存在")
    roles = role_index(store)
    target = load_principal(store, user, include_disabled=True, roles=roles)
    projects = {p["id"]: p for p in store.list_projects()}
    namespace_ids = sorted(target.namespace_perms)
    namespaces = {n["id"]: n for n in (store.list_namespaces(namespace_ids) if namespace_ids else [])}
    access = {
        "global": sorted(target.global_perms),
        "projects": [{"id": pid, "name": projects[pid]["name"] if pid in projects else f"#{pid}",
                      "perms": sorted(target.permissions_in(PROJECT, pid))} for pid in sorted(target.project_perms)],
        "namespaces": [{"id": nid, "name": namespaces[nid]["name"] if nid in namespaces else f"#{nid}",
                        "cluster": namespaces[nid]["cluster_name"] if nid in namespaces else "",
                        "tier": namespaces[nid]["tier"] if nid in namespaces else "",
                        "perms": sorted(target.permissions_in(NAMESPACE, nid))} for nid in namespace_ids],
    }
    return dict(account_view(user, target, roles), can_manage=manage_denied(me, target) is None,
                bindings=grant_rules.visible_binding_rows(store, me, user_id=user_id), access=access)


# ------------------------------------------------------------------ 新建 / 修改 / 删除
def _bool(data: Dict[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} 必须是布尔值")
    return value


def _legacy_role(data: Dict[str, Any]) -> Optional[str]:
    role = data.get("role")
    if role is None:
        return None
    if not isinstance(role, str) or role not in LEGACY_ROLES:
        raise ValueError("角色无效")
    return role


def create_account(store: Store, auth: AuthService, me: Principal, data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("请求格式错误")
    reject_unknown(data, CREATE_FIELDS, "账号请求包含未知字段")
    username = text_field(data, "username", 32)
    if not USERNAME_RE.fullmatch(username):
        raise ValueError("用户名为 2–32 位字母、数字、_ . @ -，以字母或数字开头")
    if store.get_user_by_name(username):
        raise Conflict("用户名已存在")
    password = data.get("password", "")
    if not isinstance(password, str):
        raise ValueError("密码格式无效")
    auth.validate_password(password)

    legacy = _legacy_role(data)
    platform_admin = _bool(data, "platform_admin", legacy == "admin")
    requested = data.get("grants", [])
    if not isinstance(requested, list) or len(requested) > MAX_INITIAL_GRANTS:
        raise ValueError(f"初始授权必须是数组，最多 {MAX_INITIAL_GRANTS} 项")
    if legacy in ("developer", "viewer") and not requested:
        requested = [{"role": LEGACY_ROLES[legacy], "scope_type": PROJECT, "scope_id": store.default_project_id()}]
    if platform_admin and not me.is_platform_admin:
        raise Forbidden("只有平台管理员可以创建平台管理员账号")
    roles = role_index(store)
    initial, seen = [], set()
    for item in requested:
        if isinstance(item, dict):
            reject_unknown(item, {"role", "scope_type", "scope_id", "expires_at", "note"}, "初始授权包含未知字段")
        values = grant_rules.normalize_grant(store, me, item, roles)
        key = (values["role"], values["scope_type"], values["scope_id"])
        if key not in seen:
            seen.add(key)
            initial.append(values)

    user = store.create_user(
        username=username, display_name=text_field(data, "display_name") or username,
        email=text_field(data, "email", 254), role="admin" if platform_admin else "member",
        password_hash=hash_password(password), enabled=1, must_change_password=1,
        grants=initial, created_by=me.id)
    return view_of(store, user)


def update_account(store: Store, me: Principal, user_id: int, data: Any) -> Tuple[Dict[str, Any], List[str]]:
    """修改资料、启用状态与平台管理员身份；其他授权保持不变。返回 (账号, 变化的字段)。"""
    if not isinstance(data, dict):
        raise ValueError("请求格式错误")
    reject_unknown(data, UPDATE_FIELDS, "账号请求包含未知字段")
    user, target = _target(store, me, user_id)
    enabled = _bool(data, "enabled", bool(user["enabled"]))
    legacy = _legacy_role(data)
    default_admin = target.is_platform_admin if legacy is None else legacy == "admin"
    platform_admin = _bool(data, "platform_admin", default_admin)
    display = text_field(data, "display_name") if "display_name" in data else user.get("display_name") or ""
    email = text_field(data, "email", 254) if "email" in data else user.get("email") or ""

    admin_changed = platform_admin != target.is_platform_admin
    if user_id == me.id and (not enabled or admin_changed):
        raise ValueError("不能停用自己，也不能修改自己的平台管理员身份")
    if admin_changed and not me.is_platform_admin:
        raise Forbidden("只有平台管理员可以授予或撤销平台管理员身份")
    losing_admin = target.is_platform_admin and user["enabled"] and (not enabled or not platform_admin)
    if losing_admin and not store.count_platform_admins(exclude_id=user_id):
        raise ValueError("至少保留一个启用的平台管理员")

    changes = [name for name, changed in (("display_name", display != (user.get("display_name") or "")),
                                          ("email", email != (user.get("email") or "")),
                                          ("enabled", enabled != bool(user["enabled"])),
                                          ("platform_admin", admin_changed)) if changed]
    store.update_user(user_id, display_name=display or user["username"], email=email, enabled=int(enabled))
    if admin_changed:
        store.set_platform_admin(user_id, platform_admin, me.id)
    if not enabled and user["enabled"]:
        store.delete_user_sessions(user_id)
        store.delete_user_api_tokens(user_id)
    return view_of(store, store.get_user(user_id)), changes  # type: ignore[arg-type]


def delete_account(store: Store, me: Principal, user_id: int) -> Dict[str, Any]:
    user, target = _target(store, me, user_id)
    if user_id == me.id:
        raise ValueError("不能删除自己的账号")
    if target.is_platform_admin and user["enabled"] and not store.count_platform_admins(exclude_id=user_id):
        raise ValueError("至少保留一个启用的平台管理员")
    store.delete_user(user_id)
    return user


def reset_password(store: Store, auth: AuthService, me: Principal, user_id: int, data: Any) -> Tuple[Dict[str, Any], str]:
    user, _ = _target(store, me, user_id)
    if user_id == me.id:
        raise ValueError("请在“我的账号”修改自己的密码")
    password = (data or {}).get("password") if isinstance(data, dict) else None
    if password is not None and not isinstance(password, str):
        raise ValueError("密码格式无效")
    result = auth.reset_password(user_id, password or None)
    auth.unlock(user["username"])
    return user, result


# ------------------------------------------------------------------ 当前账号
def session_profile(store: Store, settings: Settings, me: Principal) -> Dict[str, Any]:
    """/api/auth/me：账号信息、各范围的有效权限，以及可见的项目与命名空间。

    permissions 是在任一范围内拥有的权限（用于菜单显示）；按钮是否可用应使用 global_permissions 或
    projects[].perms / namespaces[].perms 判断具体资源。
    """
    roles = role_index(store)
    project_ids = me.project_ids("project.view")
    projects = [{"id": p["id"], "name": p["name"], "slug": p["slug"],
                 "perms": sorted(me.permissions_in(PROJECT, p["id"]))}
                for p in store.list_projects() if project_ids is None or p["id"] in project_ids]
    namespace_ids = me.namespace_ids("namespace.view")
    namespaces = [{"id": n["id"], "name": n["name"], "cluster": n["cluster_name"], "tier": n["tier"],
                   "perms": sorted(me.permissions_in(NAMESPACE, n["id"]))}
                  for n in (store.list_namespaces(namespace_ids) if namespace_ids is None or namespace_ids else [])]
    return dict(account_view(me.user, me, roles), permissions=sorted(me.all_permissions),
                global_permissions=sorted(me.global_perms), site_name=settings.general.site_name,
                deploy_enabled=settings.general.deploy_enabled,
                bindings=[grant_rules.binding_view(b) for b in me.bindings], projects=projects, namespaces=namespaces)


def require_member_admin(me: Principal) -> None:
    if not grant_rules.can_manage_members(me):
        raise Forbidden("没有任何可管理的成员范围")


__all__ = ["account_view", "view_of", "manage_denied", "list_accounts", "account_detail", "create_account",
           "update_account", "delete_account", "reset_password", "session_profile", "require_member_admin"]
