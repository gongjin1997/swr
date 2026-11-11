"""在路由定义处声明权限。

用法（写在 @router.<method> 下面）::

    @router.get("/api/apps")
    @requires("app.view")
    async def list_apps(...): ...

- PUBLIC_ROUTE：无需登录
- AUTHENTICATED_ROUTE：登录即可，具体资源权限由处理函数检查
- 权限码：中间件先做粗检查（全局权限要求全局授权；项目/命名空间权限要求至少在一个范围内拥有），
  处理函数再按具体资源检查

本模块不依赖其他业务模块，api/* 可以放心导入。
"""
from __future__ import annotations

from typing import Callable, TypeVar

PUBLIC_ROUTE = "public"
AUTHENTICATED_ROUTE = "authenticated"
ATTRIBUTE = "__route_permission__"

F = TypeVar("F", bound=Callable)


def requires(permission: str) -> Callable[[F], F]:
    def mark(func: F) -> F:
        setattr(func, ATTRIBUTE, permission)
        return func
    return mark


public = requires(PUBLIC_ROUTE)
authenticated = requires(AUTHENTICATED_ROUTE)
