"""API 路由权限表。

每个 /api 路由都必须在 ROUTE_PERMISSIONS 中登记，未登记的路由一律拒绝（fail closed）。
值的含义：
- PUBLIC_ROUTE：无需登录
- AUTHENTICATED_ROUTE：登录即可，具体资源权限由处理函数检查
- 权限点：中间件先做一次全局或作用域检查，见 SCOPED_PERMISSIONS
"""
from __future__ import annotations

from typing import Optional

from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.routing import Match

PUBLIC_ROUTE = "public"
AUTHENTICATED_ROUTE = "authenticated"

# 作用域权限：中间件只要求用户至少在一个项目/命名空间上拥有该权限，处理函数再检查具体资源
SCOPED_PERMISSIONS = frozenset({
    "project.view", "app.view", "app.manage", "build.run", "job.view", "job.cancel.own",
    "deploy.run", "deploy.view", "namespace.view",
})

_A = AUTHENTICATED_ROUTE

ROUTE_PERMISSIONS = {
    # 认证与个人账号
    ("GET", "/api/auth/config"): PUBLIC_ROUTE,
    ("POST", "/api/auth/login"): PUBLIC_ROUTE,
    ("GET", "/api/auth/me"): _A,
    ("POST", "/api/auth/logout"): _A,
    ("PUT", "/api/account/profile"): _A,
    ("PUT", "/api/account/password"): _A,
    ("GET", "/api/account/sessions"): _A,
    ("DELETE", "/api/account/sessions/{id}"): _A,
    ("GET", "/api/account/tokens"): _A,
    ("POST", "/api/account/tokens"): _A,
    ("DELETE", "/api/account/tokens/{id}"): _A,
    # 账号管理
    ("GET", "/api/roles"): "user.manage",
    ("GET", "/api/users"): "user.manage",
    ("POST", "/api/users"): "user.manage",
    ("PUT", "/api/users/{id}"): "user.manage",
    ("DELETE", "/api/users/{id}"): "user.manage",
    ("POST", "/api/users/{id}/reset-password"): "user.manage",
    # 系统
    ("GET", "/api/settings"): "settings.manage",
    ("PUT", "/api/settings"): "settings.manage",
    ("POST", "/api/settings/registry/test"): "settings.manage",
    ("GET", "/api/audit"): "audit.view",
    ("GET", "/api/system"): "settings.manage",
    ("POST", "/api/cloud/hcs/test"): "settings.manage",
    ("GET", "/api/cloud/hcs/capabilities"): "settings.manage",
    ("GET", "/api/info"): _A,
    ("POST", "/api/registry/test"): "settings.manage",
    # 旧部署目标与发布记录
    ("GET", "/api/deploy/providers"): "deploy.view",
    ("GET", "/api/deploy/targets"): "deploy.view",
    ("POST", "/api/deploy/targets"): "deploy.manage",
    ("PUT", "/api/deploy/targets/{id}"): "deploy.manage",
    ("DELETE", "/api/deploy/targets/{id}"): "deploy.manage",
    ("POST", "/api/deploy/targets/{id}/test"): "deploy.manage",
    ("POST", "/api/deployments"): "deploy.run",
    ("GET", "/api/deployments"): _A,
    ("GET", "/api/deployments/{id}"): _A,
    ("GET", "/api/deployments/{id}/log"): _A,
    ("POST", "/api/deployments/{id}/approve"): _A,
    ("POST", "/api/deployments/{id}/reject"): _A,
    # 项目、集群、命名空间
    ("GET", "/api/projects"): "project.view",
    ("POST", "/api/projects"): "project.manage",
    ("GET", "/api/projects/{id}"): "project.view",
    ("PUT", "/api/projects/{id}"): "project.manage",
    ("DELETE", "/api/projects/{id}"): "project.manage",
    ("PUT", "/api/projects/{id}/namespaces/{namespace_id}"): "project.manage",
    ("DELETE", "/api/projects/{id}/namespaces/{namespace_id}"): "project.manage",
    ("GET", "/api/clusters"): "cluster.manage",
    ("POST", "/api/clusters"): "cluster.manage",
    ("GET", "/api/clusters/{id}"): "cluster.manage",
    ("PUT", "/api/clusters/{id}"): "cluster.manage",
    ("POST", "/api/clusters/{id}/test"): "cluster.manage",
    ("POST", "/api/clusters/{id}/rotate"): "cluster.manage",
    ("POST", "/api/clusters/{id}/revoke"): "cluster.manage",
    ("GET", "/api/namespaces"): "namespace.view",
    ("POST", "/api/namespaces"): "cluster.manage",
    ("GET", "/api/namespaces/{id}"): "namespace.view",
    ("PUT", "/api/namespaces/{id}"): "cluster.manage",
    ("POST", "/api/namespaces/{id}/review"): "cluster.manage",
    ("GET", "/api/namespaces/{id}/rbac-yaml"): "cluster.manage",
    # 服务（命名空间作用域权限在处理函数中检查）
    ("GET", "/api/services"): _A,
    ("POST", "/api/services"): _A,
    ("GET", "/api/services/{id}"): _A,
    ("PUT", "/api/services/{id}"): _A,
    ("DELETE", "/api/services/{id}"): _A,
    ("GET", "/api/services/{id}/revisions"): _A,
    ("GET", "/api/services/{id}/revisions/diff"): _A,
    ("GET", "/api/services/{id}/revisions/{revision_id}"): _A,
    ("POST", "/api/services/{id}/render"): _A,
    ("PUT", "/api/services/{id}/secrets/{key}"): _A,
    ("POST", "/api/services/{id}/deployments"): _A,
    ("POST", "/api/services/{id}/rollback"): _A,
    ("POST", "/api/services/{id}/restart"): _A,
    ("POST", "/api/services/{id}/scale"): _A,
    # 成员授权
    ("GET", "/api/bindings"): _A,
    ("POST", "/api/bindings"): _A,
    ("DELETE", "/api/bindings/{id}"): _A,
    ("GET", "/api/bindable-users"): _A,
    # 应用、构建、任务
    ("GET", "/api/app-defaults"): "app.view",
    ("GET", "/api/apps"): "app.view",
    ("POST", "/api/apps"): "app.manage",
    ("PUT", "/api/apps/{app_id}"): "app.manage",
    ("DELETE", "/api/apps/{app_id}"): "app.manage",
    ("POST", "/api/dockerfile/import"): "app.manage",
    ("GET", "/api/apps/{app_id}/versions"): "app.view",
    ("POST", "/api/apps/{app_id}/preview"): "app.view",
    ("POST", "/api/builds"): "build.run",
    ("GET", "/api/jobs"): "job.view",
    ("GET", "/api/jobs/{job_id}"): "job.view",
    ("GET", "/api/jobs/{job_id}/log"): "job.view",
    ("POST", "/api/jobs/{job_id}/cancel"): "job.cancel.own",
}


def route_permission(app: FastAPI, path: str, method: str) -> Optional[str]:
    """返回请求命中的 /api 路由所登记的权限；未命中或未登记返回 None。"""
    scope = {"type": "http", "path": path, "root_path": "", "method": method}
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/"):
            continue
        match, _ = route.matches(scope)
        if match is Match.FULL:
            return ROUTE_PERMISSIONS.get((method, route.path))
    return None
