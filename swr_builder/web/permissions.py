"""API 路由权限表。

权限在路由定义处用 @requires(...) 声明（见 web/guard.py），这里汇总成按注册顺序排列的路由表，供中间件在
路由分发前匹配。规则：
- 每个 /api 路由都必须声明权限，且权限码必须在 access/catalog.py 登记，否则导入时直接报错；
- 运行时未匹配到任何已登记路由的 /api 路径一律拒绝（fail closed）。
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from starlette.routing import compile_path

from ..access import PERMISSIONS
from ..api import ROUTERS
from .guard import ATTRIBUTE, AUTHENTICATED_ROUTE, PUBLIC_ROUTE

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


ROUTE_TABLE: List[RouteEntry] = collect_route_permissions(ROUTERS)
ROUTE_PERMISSIONS: Dict[Tuple[str, str], str] = {(method, path): perm for method, path, perm in ROUTE_TABLE}
_ROUTE_PATTERNS = tuple((method, compile_path(path)[0], perm) for method, path, perm in ROUTE_TABLE)


def route_permission(app: Optional[FastAPI], path: str, method: str) -> Optional[str]:
    """返回匹配的 /api 路由声明的权限；未匹配返回 None。app 参数为兼容旧调用方保留。"""
    for route_method, pattern, permission in _ROUTE_PATTERNS:
        if method == route_method and pattern.fullmatch(path):
            return permission
    return None


__all__ = ["PUBLIC_ROUTE", "AUTHENTICATED_ROUTE", "ROUTE_PERMISSIONS", "ROUTE_TABLE", "collect_route_permissions",
           "route_permission"]
