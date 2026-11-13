"""API 路由权限表。

权限在路由定义处用 @requires(...) 声明（见 web/guard.py），这里汇总成按注册顺序排列的路由表，供中间件在
路由分发前匹配。规则：
- 每个 /api 路由都必须声明权限，且权限码必须在 access/catalog.py 登记，否则导入时直接报错；
- 运行时未匹配到任何已登记路由的 /api 路径一律拒绝（fail closed）；
- 用 @deploy_module 标记的路由属于部署模块，系统设置关闭“启用部署中心”时一律拒绝。
"""
from __future__ import annotations

from typing import Dict, FrozenSet, Iterable, List, Optional, Tuple

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from starlette.routing import compile_path

from ..access import PERMISSIONS
from ..api import ROUTERS
from .guard import ATTRIBUTE, AUTHENTICATED_ROUTE, DEPLOY_MODULE_ATTRIBUTE, PUBLIC_ROUTE

RouteEntry = Tuple[str, str, str]  # (method, path, permission)


def collect_route_permissions(routers: Iterable[APIRouter]) -> List[RouteEntry]:
    entries: List[RouteEntry] = []
    for router in routers:
        for route in router.routes:
            if not isinstance(route, APIRoute) or not route.path.startswith("/api/"):
                continue
            permission = getattr(route.endpoint, ATTRIBUTE, None)
            if permission is None:
                raise RuntimeError(f"API 路由 {sorted(route.methods)} {route.path} 未声明权限（@requires）")
            if permission not in (PUBLIC_ROUTE, AUTHENTICATED_ROUTE) and permission not in PERMISSIONS:
                raise RuntimeError(f"API 路由 {route.path} 使用了未登记的权限 {permission}")
            for method in sorted(route.methods):
                entries.append((method, route.path, permission))
    return entries


def collect_deploy_module_routes(routers: Iterable[APIRouter]) -> FrozenSet[Tuple[str, str]]:
    """用 @deploy_module 标记的路由 (method, path)。"""
    return frozenset((method, route.path) for router in routers for route in router.routes
                     if isinstance(route, APIRoute) and getattr(route.endpoint, DEPLOY_MODULE_ATTRIBUTE, False)
                     for method in route.methods)


ROUTE_TABLE: List[RouteEntry] = collect_route_permissions(ROUTERS)
ROUTE_PERMISSIONS: Dict[Tuple[str, str], str] = {(method, path): perm for method, path, perm in ROUTE_TABLE}
DEPLOY_MODULE_ROUTES: FrozenSet[Tuple[str, str]] = collect_deploy_module_routes(ROUTERS)
_ROUTE_PATTERNS = tuple((method, compile_path(path)[0], perm, (method, path) in DEPLOY_MODULE_ROUTES)
                        for method, path, perm in ROUTE_TABLE)


def match_route(path: str, method: str) -> Optional[Tuple[str, bool]]:
    """匹配 /api 路由：返回 (声明的权限, 是否属于部署模块)；未匹配返回 None。"""
    for route_method, pattern, permission, deploy in _ROUTE_PATTERNS:
        if method == route_method and pattern.fullmatch(path):
            return permission, deploy
    return None


def route_permission(app: Optional[FastAPI], path: str, method: str) -> Optional[str]:
    """返回匹配的 /api 路由声明的权限；未匹配返回 None。app 参数为兼容旧调用方保留。"""
    matched = match_route(path, method)
    return matched[0] if matched else None


__all__ = ["PUBLIC_ROUTE", "AUTHENTICATED_ROUTE", "DEPLOY_MODULE_ROUTES", "ROUTE_PERMISSIONS", "ROUTE_TABLE",
           "collect_deploy_module_routes", "collect_route_permissions", "match_route", "route_permission"]
