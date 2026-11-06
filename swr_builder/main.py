"""兼容入口：v1.2 及之前从这里导入 create_app 与路由权限表。新代码请使用 swr_builder.app / swr_builder.web.permissions。"""
from .app import create_app
from .web.permissions import AUTHENTICATED_ROUTE, PUBLIC_ROUTE, ROUTE_PERMISSIONS, route_permission

__all__ = ["create_app", "AUTHENTICATED_ROUTE", "PUBLIC_ROUTE", "ROUTE_PERMISSIONS", "route_permission"]
