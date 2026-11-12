"""HTTP 路由：每个领域一个 APIRouter，只做参数解析、调用服务层、组装响应。"""
from fastapi import FastAPI

from . import (apps, auth, bindings, clusters, deployments, environments, preset_api, projects, resource_api, roles,
               services, system, users)

ROUTERS = [auth.router, users.router, roles.router, projects.router, clusters.router, environments.router,
           services.router, bindings.router, resource_api.router, resource_api.service_bindings_router,
           preset_api.router, system.router, deployments.router, apps.router]


def include_routers(app: FastAPI) -> None:
    for router in ROUTERS:
        app.include_router(router)
