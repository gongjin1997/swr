"""访问控制：权限目录（catalog）与请求主体（principal）。

业务规则（谁能管理账号、谁能授予哪个角色）在 services/accounts.py、services/grants.py。
"""
from .catalog import (BUILTIN_ROLES, GLOBAL, GROUPS, MEMBER_MANAGE_PERMISSIONS, NAMESPACE, PERMISSIONS,
                      PLATFORM_ADMIN_PERMISSION, PLATFORM_ADMIN_ROLE, PROJECT, SCOPE_LABELS, SCOPE_TYPES, WILDCARD,
                      Permission, RoleDef, expand, global_only, grantable_permissions)
from .principal import Principal, build_principal, load_principal, principals_for, role_index, same_user

__all__ = [
    "BUILTIN_ROLES", "GLOBAL", "GROUPS", "MEMBER_MANAGE_PERMISSIONS", "NAMESPACE", "PERMISSIONS",
    "PLATFORM_ADMIN_PERMISSION", "PLATFORM_ADMIN_ROLE", "PROJECT", "SCOPE_LABELS", "SCOPE_TYPES", "WILDCARD",
    "Permission", "RoleDef", "expand", "global_only", "grantable_permissions",
    "Principal", "build_principal", "load_principal", "principals_for", "role_index", "same_user",
]
