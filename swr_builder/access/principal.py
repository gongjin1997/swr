"""请求主体：一个账号在本次请求中的全部有效权限。

每个请求只加载一次（账号的有效授权 + 自定义角色两次查询），之后的权限判断都在内存中完成。
HTTP 中间件、业务服务和后台 worker 使用同一套判断，避免各处各写一份。

判断规则：
- 只有启用的账号才有权限；授权必须未撤销、未过期，角色必须存在、已启用且与授权范围一致。
- 全局授权对所有项目和命名空间生效；项目授权与命名空间授权互不传递。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Tuple

from .catalog import BUILTIN_ROLES, GLOBAL, NAMESPACE, PLATFORM_ADMIN_PERMISSION, PROJECT, PERMISSIONS, RoleDef

User = Dict[str, Any]
Binding = Dict[str, Any]


@dataclass(frozen=True)
class Principal:
    user: User
    bindings: Tuple[Binding, ...] = ()
    global_perms: FrozenSet[str] = frozenset()
    project_perms: Mapping[int, FrozenSet[str]] = field(default_factory=dict)
    namespace_perms: Mapping[int, FrozenSet[str]] = field(default_factory=dict)

    @property
    def id(self) -> int:
        return self.user["id"]

    @property
    def username(self) -> str:
        return self.user["username"]

    @property
    def is_platform_admin(self) -> bool:
        return PLATFORM_ADMIN_PERMISSION in self.global_perms

    # ------------------------------------------------------------------ 判断
    def can(self, permission: str) -> bool:
        """全局权限。"""
        return permission in self.global_perms

    def can_project(self, project_id: Optional[int], permission: str) -> bool:
        return permission in self.global_perms or permission in self.project_perms.get(project_id or 0, ())

    def can_namespace(self, namespace_id: Optional[int], permission: str) -> bool:
        return permission in self.global_perms or permission in self.namespace_perms.get(namespace_id or 0, ())

    def can_in(self, scope_type: str, scope_id: int, permission: str) -> bool:
        if scope_type == PROJECT:
            return self.can_project(scope_id, permission)
        if scope_type == NAMESPACE:
            return self.can_namespace(scope_id, permission)
        return self.can(permission)

    def can_anywhere(self, permission: str) -> bool:
        """在任一范围内拥有该权限（菜单与路由粗检查用；具体资源由处理函数再判断）。"""
        return (permission in self.global_perms
                or any(permission in perms for perms in self.project_perms.values())
                or any(permission in perms for perms in self.namespace_perms.values()))

    # ------------------------------------------------------------------ 范围
    def project_ids(self, permission: str) -> Optional[List[int]]:
        """拥有该权限的项目 ID；全局拥有时返回 None，表示全部项目。"""
        if permission in self.global_perms:
            return None
        return sorted(pid for pid, perms in self.project_perms.items() if permission in perms)

    def namespace_ids(self, permission: str) -> Optional[List[int]]:
        """拥有该权限的命名空间 ID；全局拥有时返回 None，表示全部命名空间。"""
        if permission in self.global_perms:
            return None
        return sorted(nid for nid, perms in self.namespace_perms.items() if permission in perms)

    def permissions_in(self, scope_type: str, scope_id: int) -> FrozenSet[str]:
        """在某个范围内的有效权限（含全局授权）。"""
        if scope_type == PROJECT:
            return self.global_perms | self.project_perms.get(scope_id, frozenset())
        if scope_type == NAMESPACE:
            return self.global_perms | self.namespace_perms.get(scope_id, frozenset())
        return self.global_perms

    @property
    def all_permissions(self) -> FrozenSet[str]:
        result = set(self.global_perms)
        for perms in list(self.project_perms.values()) + list(self.namespace_perms.values()):
            result.update(perms)
        return frozenset(result)


def custom_role_defs(rows: Iterable[Dict[str, Any]]) -> Dict[str, RoleDef]:
    return {row["code"]: RoleDef(row["code"], row["name"], row["scope_type"], frozenset(row["permissions"]),
                                 row.get("description") or "", builtin=False, enabled=bool(row["enabled"]))
            for row in rows}


def role_index(store: Any) -> Dict[str, RoleDef]:
    """内置角色 + 自定义角色（含停用的）。自定义角色不能使用内置角色的标识。"""
    roles = dict(custom_role_defs(store.list_custom_roles()))
    roles.update(BUILTIN_ROLES)
    return roles


def build_principal(user: User, bindings: Iterable[Binding], roles: Mapping[str, RoleDef]) -> Principal:
    global_perms: set = set()
    projects: Dict[int, set] = {}
    namespaces: Dict[int, set] = {}
    effective: List[Binding] = []
    for binding in bindings:
        role = roles.get(binding["role"])
        if role is None or not role.enabled or role.scope != binding["scope_type"]:
            continue
        perms = role.effective
        if binding["scope_type"] == GLOBAL:
            global_perms.update(perms)
        elif binding["scope_type"] == PROJECT:
            projects.setdefault(int(binding["scope_id"]), set()).update(perms)
        elif binding["scope_type"] == NAMESPACE:
            namespaces.setdefault(int(binding["scope_id"]), set()).update(perms)
        else:
            continue
        effective.append(binding)
    return Principal(user, tuple(effective), frozenset(global_perms),
                     {k: frozenset(v) for k, v in projects.items()},
                     {k: frozenset(v) for k, v in namespaces.items()})


def load_principal(store: Any, user: Optional[User], *, include_disabled: bool = False,
                   roles: Optional[Mapping[str, RoleDef]] = None) -> Principal:
    """读取账号的有效权限。include_disabled=True 时按"如果启用"计算，用于账号管理时判断对方持有的权限。"""
    if not user:
        return Principal({"id": 0, "username": ""})
    if not user.get("enabled") and not include_disabled:
        return Principal(user)
    return build_principal(user, store.list_bindings(user_id=user["id"]), roles if roles is not None else role_index(store))


def principals_for(store: Any, users: Iterable[User]) -> Dict[int, Principal]:
    """批量计算多个账号的权限（含停用账号），只查询一次授权表。"""
    roles = role_index(store)
    grouped: Dict[int, List[Binding]] = {}
    for binding in store.list_bindings():
        grouped.setdefault(int(binding["subject_id"]), []).append(binding)
    return {user["id"]: build_principal(user, grouped.get(user["id"], []), roles) for user in users}


def same_user(a: Any, b: Any) -> bool:
    """比较账号 ID。jobs/deployments.created_by 是 TEXT 列，读出来是字符串。"""
    return a is not None and b is not None and str(a) == str(b)


__all__ = ["Principal", "build_principal", "load_principal", "principals_for", "role_index", "custom_role_defs",
           "same_user", "PERMISSIONS"]
