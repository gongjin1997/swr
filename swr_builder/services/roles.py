"""角色目录（内置 + 自定义）与自定义角色校验。"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from ..access import (BUILTIN_ROLES, GROUPS, PERMISSIONS, SCOPE_LABELS, SCOPE_TYPES, RoleDef, expand,
                      grantable_permissions, role_index)
from ..errors import Conflict, NotFound
from ..store import Store
from ..validation import reject_unknown, text_field

ROLE_FIELDS = {"code", "name", "scope_type", "permissions", "description", "enabled"}
CODE_RE = re.compile(r"[a-z][a-z0-9_]{2,47}")


def role_view(role: RoleDef, binding_count: int = 0) -> Dict[str, Any]:
    return {"code": role.code, "name": role.name, "scope_type": role.scope, "permissions": sorted(role.effective),
            "description": role.description, "enabled": role.enabled, "builtin": role.builtin,
            "binding_count": binding_count}


def role_catalog(store: Store) -> List[Dict[str, Any]]:
    """内置角色在前（按范围），自定义角色在后。"""
    counts = store.count_role_bindings()
    roles = role_index(store)
    builtin = [roles[code] for code in BUILTIN_ROLES]
    custom = sorted((r for r in roles.values() if not r.builtin), key=lambda r: (SCOPE_TYPES.index(r.scope), r.name))
    return [role_view(role, counts.get(role.code, 0)) for role in builtin + custom]


def catalog_payload(store: Store) -> Dict[str, Any]:
    """角色目录 + 权限目录，前端角色/授权页面使用。"""
    return {
        "items": role_catalog(store),
        "permissions": {code: p.label for code, p in PERMISSIONS.items()},
        "scope_permissions": {scope: grantable_permissions(scope) for scope in SCOPE_TYPES},
        "permission_catalog": [{"code": p.code, "label": p.label, "group": p.group, "scopes": list(p.scopes),
                                "implies": list(p.implies), "grantable": p.grantable} for p in PERMISSIONS.values()],
        "groups": [{"code": code, "label": label} for code, label in GROUPS.items()],
        "scopes": [{"code": code, "label": SCOPE_LABELS[code]} for code in SCOPE_TYPES],
    }


def normalize_role(data: Any, current: Optional[Dict[str, Any]] = None) -> Tuple[str, str, str, List[str], str, bool]:
    """校验自定义角色，返回 (code, name, scope, permissions, description, enabled)。

    permissions 会补齐隐含的查看权限（如 app.manage → app.view），保存的是展开后的列表。
    """
    if not isinstance(data, dict):
        raise ValueError("角色请求格式错误")
    reject_unknown(data, ROLE_FIELDS, "角色包含未知字段")
    code = text_field(data, "code", 48) or (current or {}).get("code", "")
    name = text_field(data, "name", 80)
    scope = data.get("scope_type", (current or {}).get("scope_type"))
    grants = data.get("permissions")
    enabled = data.get("enabled", True)
    if not CODE_RE.fullmatch(code) or code in BUILTIN_ROLES:
        raise ValueError("角色标识需为 3–48 位小写字母、数字或下划线，以字母开头，且不能使用内置角色标识")
    if not name or scope not in SCOPE_TYPES:
        raise ValueError("请填写角色名称并选择授权范围")
    if current and (code != current["code"] or scope != current["scope_type"]):
        raise ValueError("已有角色的标识和授权范围不能修改")
    allowed = set(grantable_permissions(scope))
    if (not isinstance(grants, list) or not grants or any(not isinstance(p, str) for p in grants)
            or set(grants) - allowed):
        raise ValueError("请选择与角色授权范围匹配的权限")
    if not isinstance(enabled, bool):
        raise ValueError("enabled 必须为布尔值")
    permissions = sorted(expand(grants, scope))
    return code, name, scope, permissions, text_field(data, "description", 300), enabled


def create_role(store: Store, data: Any) -> Dict[str, Any]:
    values = normalize_role(data)
    if values[0] in role_index(store):
        raise Conflict("角色标识已存在")
    store.save_custom_role(*values)
    return find_role(store, values[0])


def update_role(store: Store, code: str, data: Any) -> Dict[str, Any]:
    current = next((r for r in store.list_custom_roles() if r["code"] == code), None)
    if not current:
        raise NotFound("自定义角色不存在；内置角色不可修改")
    store.save_custom_role(*normalize_role(data, current))
    return find_role(store, code)


def delete_role(store: Store, code: str) -> None:
    if not any(r["code"] == code for r in store.list_custom_roles()):
        raise NotFound("自定义角色不存在；内置角色不可删除")
    if not store.delete_custom_role(code):
        raise Conflict("角色仍有有效授权，请先撤销成员授权或停用角色")


def find_role(store: Store, code: str) -> Dict[str, Any]:
    return next(r for r in role_catalog(store) if r["code"] == code)
