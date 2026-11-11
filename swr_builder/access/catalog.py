"""权限目录：权限点、权限分组、授权范围与内置角色的唯一定义处。

后端鉴权、角色校验、/api/auth/me 和前端页面都从这里取数据。新增权限的步骤：
1. 在 PERMISSIONS 登记（声明可授予的范围与隐含的查看权限）；
2. 需要时加入内置角色；
3. 在路由上用 @requires("新权限") 声明。

授权范围（scope）：
- global     全局授权，对所有项目与命名空间生效
- project    只对一个项目生效（应用、构建、项目成员）
- namespace  只对一个命名空间生效（服务、发布、审批、命名空间成员）
项目授权不会自动带来命名空间权限，反之亦然。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, List, Tuple

GLOBAL, PROJECT, NAMESPACE = "global", "project", "namespace"
SCOPE_TYPES: Tuple[str, ...] = (GLOBAL, PROJECT, NAMESPACE)
SCOPE_LABELS: Dict[str, str] = {GLOBAL: "全局", PROJECT: "项目", NAMESPACE: "命名空间"}
WILDCARD = "*"

PLATFORM_ADMIN_ROLE = "platform_admin"
PLATFORM_ADMIN_PERMISSION = "platform.admin"
# 项目/命名空间角色总是包含查看该范围的权限：看不到的资源无法操作
SCOPE_BASE_PERMISSIONS: Dict[str, Tuple[str, ...]] = {GLOBAL: (), PROJECT: ("project.view",),
                                                      NAMESPACE: ("namespace.view",)}
# 持有这些权限的人可以再给别人授权；非平台管理员不能授予包含它们的角色，避免授权链无限扩散
MEMBER_MANAGE_PERMISSIONS: Dict[str, str] = {PROJECT: "project.member.manage", NAMESPACE: "namespace.member.manage"}


@dataclass(frozen=True)
class Permission:
    code: str
    label: str
    group: str
    scopes: Tuple[str, ...]
    implies: Tuple[str, ...] = ()
    # False：只能随内置角色获得，不能写进自定义角色（平台管理员确认、已停用的旧部署接口）
    grantable: bool = True


GROUPS: Dict[str, str] = {
    "platform": "平台管理",
    "account": "账号与权限",
    "project": "项目与应用",
    "build": "构建",
    "namespace": "集群与命名空间",
    "service": "服务与发布",
    "legacy": "旧部署目标",
}

_G, _GP, _GN, _GPN = (GLOBAL,), (GLOBAL, PROJECT), (GLOBAL, NAMESPACE), (GLOBAL, PROJECT, NAMESPACE)

PERMISSIONS: Dict[str, Permission] = {p.code: p for p in (
    Permission("platform.admin", "平台管理员确认（生产发布等）", "platform", _G, grantable=False),
    Permission("settings.manage", "修改系统设置", "platform", _G),
    Permission("audit.view", "查看操作审计", "platform", _G),
    Permission("user.manage", "管理账号", "account", _G),
    Permission("role.manage", "管理角色定义", "account", _G),
    Permission("project.view", "查看项目", "project", _GP),
    Permission("project.manage", "新建、修改、删除项目", "project", _G, ("project.view",)),
    Permission("project.member.manage", "管理项目成员", "project", _GP, ("project.view",)),
    Permission("app.view", "查看应用和版本", "project", _GP, ("project.view",)),
    Permission("app.manage", "新建、修改、删除应用", "project", _GP, ("app.view",)),
    Permission("build.run", "上传文件并构建镜像", "build", _GP, ("app.view", "job.view")),
    Permission("job.view", "查看构建记录和日志", "build", _GP, ("project.view",)),
    Permission("job.cancel.own", "取消自己提交的构建", "build", _GP, ("job.view",)),
    Permission("job.cancel", "取消任何人的构建", "build", _GP, ("job.cancel.own",)),
    Permission("cluster.manage", "管理集群、纳管命名空间与发布策略", "namespace", _G, ("namespace.view",)),
    Permission("namespace.view", "查看命名空间与服务", "namespace", _GN),
    Permission("namespace.member.manage", "管理命名空间成员", "namespace", _GN, ("namespace.view",)),
    Permission("service.config", "配置服务", "service", _GN, ("namespace.view",)),
    Permission("service.deploy", "发布服务", "service", _GN, ("namespace.view",)),
    Permission("service.operate", "运维服务（重新发布）", "service", _GN, ("namespace.view",)),
    Permission("service.rollback", "回滚服务", "service", _GN, ("namespace.view",)),
    Permission("service.delete", "删除服务", "service", _GN, ("namespace.view",)),
    Permission("secret.manage", "管理服务密钥", "service", _GN, ("namespace.view",)),
    Permission("pod.log.view", "查看 Pod 日志", "service", _GN, ("namespace.view",)),
    Permission("deploy.view", "查看发布记录", "service", _GPN),
    Permission("deploy.approve", "审批发布", "service", _GN, ("namespace.view", "deploy.view")),
    Permission("deploy.manage", "管理旧部署目标", "legacy", _G, grantable=False),
    Permission("deploy.run", "执行旧部署（已停用）", "legacy", _G, grantable=False),
)}


def expand(codes: Iterable[str], scope: str = GLOBAL) -> FrozenSet[str]:
    """展开通配符、隐含权限与范围基础权限，只保留在 scope 范围内有效的权限。未登记的权限码被丢弃（fail closed）。"""
    base = SCOPE_BASE_PERMISSIONS.get(scope, ())
    result: set = set()
    pending = list(codes)
    while pending:
        code = pending.pop()
        if code == WILDCARD:
            pending.extend(PERMISSIONS)
            continue
        permission = PERMISSIONS.get(code)
        if permission is None or code in result or scope not in permission.scopes:
            continue
        result.add(code)
        pending.extend(permission.implies)
        if not pending and base and result.isdisjoint(base):
            pending.extend(base)  # 至少有一个有效权限时，再补上该范围的查看权限
    return frozenset(result)


def global_only(code: str) -> bool:
    permission = PERMISSIONS.get(code)
    return permission is not None and permission.scopes == _G


def grantable_permissions(scope: str) -> List[str]:
    """自定义角色在该范围内可以选择的权限。"""
    return [code for code, p in PERMISSIONS.items() if p.grantable and scope in p.scopes]


@dataclass(frozen=True)
class RoleDef:
    code: str
    name: str
    scope: str
    permissions: FrozenSet[str]
    description: str = ""
    builtin: bool = True
    enabled: bool = True

    @property
    def effective(self) -> FrozenSet[str]:
        """展开隐含权限后、在本角色范围内有效的权限。"""
        return expand(self.permissions, self.scope)


def _role(code: str, name: str, scope: str, permissions: Iterable[str], description: str) -> RoleDef:
    return RoleDef(code, name, scope, frozenset(permissions), description)


BUILTIN_ROLES: Dict[str, RoleDef] = {r.code: r for r in (
    _role(PLATFORM_ADMIN_ROLE, "平台管理员", GLOBAL, {WILDCARD}, "拥有全部权限：管理账号与平台设置，确认生产发布"),
    _role("auditor", "审计员", GLOBAL,
          {"audit.view", "project.view", "app.view", "job.view", "deploy.view", "namespace.view"},
          "只读查看全部项目、命名空间、发布记录与操作审计"),
    _role("deployment_approver", "发布审批员", GLOBAL, {"deploy.approve", "deploy.view", "namespace.view"},
          "审批所有命名空间的发布；生产环境仍需平台管理员确认"),
    _role("project_admin", "项目管理员", PROJECT,
          {"project.view", "project.member.manage", "app.view", "app.manage", "build.run", "job.view",
           "job.cancel.own", "job.cancel"},
          "管理项目成员、应用与构建，可取消项目内任何构建"),
    _role("developer", "开发者", PROJECT,
          {"project.view", "app.view", "app.manage", "build.run", "job.view", "job.cancel.own"},
          "维护应用、上传制品构建镜像，取消自己的构建"),
    _role("project_viewer", "项目只读", PROJECT, {"project.view", "app.view", "job.view"},
          "只读查看项目内应用、版本与构建记录"),
    _role("namespace_owner", "命名空间管理员", NAMESPACE,
          {"namespace.view", "namespace.member.manage", "service.config", "service.deploy", "service.operate",
           "service.rollback", "service.delete", "secret.manage", "pod.log.view"},
          "管理命名空间成员、服务配置、发布、回滚与密钥"),
    _role("namespace_approver", "环境审批员", NAMESPACE, {"namespace.view", "deploy.view", "deploy.approve"},
          "审批该命名空间的发布"),
    _role("deployer", "部署员", NAMESPACE,
          {"namespace.view", "service.config", "service.deploy", "service.operate", "pod.log.view"},
          "配置并发布服务"),
    _role("namespace_viewer", "命名空间只读", NAMESPACE, {"namespace.view", "pod.log.view"},
          "只读查看命名空间与服务"),
)}
