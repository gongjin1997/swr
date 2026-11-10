"""Role catalog and scope-aware validation."""
import re

from ..auth import PERMISSION_LABELS
from ..authz import GLOBAL_ROLE_PERMISSIONS, PROJECT_ROLE_PERMISSIONS, NAMESPACE_ROLE_PERMISSIONS
from ..validation import reject_unknown, text_field

LABELS = {
    "platform_admin": "平台管理员", "auditor": "审计员", "deployment_approver": "发布审批员",
    "project_admin": "项目管理员", "developer": "开发者", "project_viewer": "项目只读",
    "namespace_owner": "命名空间管理员", "namespace_approver": "环境审批员",
    "deployer": "部署员", "namespace_viewer": "命名空间只读",
}
TABLES = {"global": GLOBAL_ROLE_PERMISSIONS, "project": PROJECT_ROLE_PERMISSIONS,
          "namespace": NAMESPACE_ROLE_PERMISSIONS}
MANAGED_PERMISSIONS = set(PERMISSION_LABELS) - {"deploy.run", "deploy.manage"}
SCOPE_PERMISSIONS = {
    "global": sorted(MANAGED_PERMISSIONS - {"platform.admin"}),
    "project": sorted(set().union(*(p for role, p in PROJECT_ROLE_PERMISSIONS.items()
                                    if role not in {"platform_admin", "auditor"})) | {"deploy.view"}),
    "namespace": sorted(set().union(*NAMESPACE_ROLE_PERMISSIONS.values())),
}


def role_catalog(store):
    result = []
    for scope, table in TABLES.items():
        for code, grants in table.items():
            if scope != "global" and code in GLOBAL_ROLE_PERMISSIONS:
                continue
            result.append({"code": code, "name": LABELS[code], "scope_type": scope,
                           "permissions": sorted(MANAGED_PERMISSIONS if "*" in grants else grants),
                           "description": "", "enabled": True, "builtin": True})
    result.extend(dict(r, builtin=False, enabled=bool(r["enabled"])) for r in store.list_custom_roles())
    counts = {}
    for binding in store.list_bindings():
        counts[binding["role"]] = counts.get(binding["role"], 0) + 1
    return [dict(role, binding_count=counts.get(role["code"], 0)) for role in result]


def normalize_role(data, current=None):
    reject_unknown(data, {"code", "name", "scope_type", "permissions", "description", "enabled"}, "角色包含未知字段")
    code = text_field(data, "code", 48) or (current or {}).get("code", "")
    name = text_field(data, "name", 80)
    scope = data.get("scope_type", (current or {}).get("scope_type"))
    grants = data.get("permissions")
    enabled = data.get("enabled", True)
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,47}", code) or code in LABELS:
        raise ValueError("角色标识需为 3–48 位小写字母、数字或下划线，不能使用内置标识")
    if not name or scope not in TABLES:
        raise ValueError("请填写角色名称并选择授权范围")
    if current and (code != current["code"] or scope != current["scope_type"]):
        raise ValueError("已有角色的标识和授权范围不能修改")
    if (not isinstance(grants, list) or not grants or any(not isinstance(p, str) for p in grants)
            or set(grants) - set(SCOPE_PERMISSIONS[scope])):
        raise ValueError("请选择与角色授权范围匹配的权限")
    if not isinstance(enabled, bool):
        raise ValueError("enabled 必须为布尔值")
    grants = set(grants)
    if scope == "namespace":
        grants.add("namespace.view")
    if scope == "project":
        grants.add("project.view")
    if "app.manage" in grants or "build.run" in grants:
        grants.add("app.view")
    if "build.run" in grants or any(p.startswith("job.cancel") for p in grants):
        grants.add("job.view")
    if "deploy.approve" in grants:
        grants.update({"namespace.view", "deploy.view"})
    return code, name, scope, sorted(grants), text_field(data, "description", 300), enabled
