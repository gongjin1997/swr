"""在路由定义处声明权限。

用法（写在 @router.<method> 下面）::

    @router.get("/api/apps")
    @requires("app.view")
    async def list_apps(...): ...

- PUBLIC_ROUTE：无需登录
- AUTHENTICATED_ROUTE：登录即可，具体资源权限由处理函数检查
- 权限码：中间件先做粗检查（全局权限要求全局授权；项目/命名空间权限要求至少在一个范围内拥有），
  处理函数再按具体资源检查

部署模块的路由（提交、审批服务发布，发布记录与日志，旧部署目标）另加 @deploy_module。
系统设置关闭“启用部署中心”时，中间件对这些路由一律返回 403 MODULE_DISABLED::

    @router.post("/api/services/{id}/rollback", status_code=202)
    @authenticated
    @deploy_module
    async def rollback_service(...): ...

本模块不依赖其他业务模块，api/* 可以放心导入。
"""
from __future__ import annotations

from typing import Callable, TypeVar

PUBLIC_ROUTE = "public"
AUTHENTICATED_ROUTE = "authenticated"
ATTRIBUTE = "__route_permission__"
DEPLOY_MODULE_ATTRIBUTE = "__deploy_module__"

F = TypeVar("F", bound=Callable)


def requires(permission: str) -> Callable[[F], F]:
    def mark(func: F) -> F:
        setattr(func, ATTRIBUTE, permission)
        return func
    return mark


def deploy_module(func: F) -> F:
    """标记部署模块的路由（写在 @router.<method> 下面）。"""
    setattr(func, DEPLOY_MODULE_ATTRIBUTE, True)
    return func


public = requires(PUBLIC_ROUTE)
authenticated = requires(AUTHENTICATED_ROUTE)
