"""应用组装：创建各个服务、注册中间件与路由、管理生命周期。"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import __version__
from .api import include_routers
from .apps import AppValidationError, normalize_app
from .auth import AuthError, AuthService
from .builder import BuildManager
from .config import Settings, load_settings
from .context import AppContext
from .deploy.manager import DeployManager
from .errors import DomainError
from .events import EventBus
from .registry import RegistryClient
from .security import SecretBox
from .services.docker import DockerStatus
from .store import Store
from .sysconfig import SysConfig
from .web import middleware, spa

logger = logging.getLogger("swr_builder")


def build_context(settings: Settings, registry_transport: Any = None, deploy_transport: Any = None,
                  hcs_transport: Any = None) -> AppContext:
    store = Store(settings.data_path / "builder.db")
    box = SecretBox.load(settings.data_path / "secret.key", os.environ.get("BUILDER_SECRET_KEY", ""))
    sysconfig = SysConfig(settings, store, box)
    sysconfig.load()
    registry = RegistryClient(settings.registry, transport=registry_transport)
    events = EventBus()
    manager = BuildManager(settings, store, registry, events)
    ctx = AppContext(settings=settings, store=store, box=box, sysconfig=sysconfig, registry=registry, events=events,
                     manager=manager, auth=AuthService(settings, store),
                     deploy=DeployManager(settings, store, box, events, deploy_transport),
                     docker=DockerStatus(settings, lambda: manager.docker_env),
                     registry_transport=registry_transport, deploy_transport=deploy_transport,
                     hcs_transport=hcs_transport)

    def on_settings_changed(paths: List[str]) -> None:
        if any(p.startswith("registry.") for p in paths):
            registry.reconfigure(settings.registry)
        if "build.max_concurrent" in paths:
            manager.resize(settings.build.max_concurrent)
        ctx.docker.invalidate()

    sysconfig.on_change.append(on_settings_changed)
    return ctx


def _import_configured_apps(ctx: AppContext) -> None:
    """首次启动且没有应用时，导入 config.yaml 中的 apps。"""
    if not ctx.settings.apps or ctx.store.list_apps():
        return
    for raw in ctx.settings.apps:
        try:
            ctx.store.create_app(normalize_app(raw))
            logger.info("已从配置文件导入应用 %s", raw.get("name"))
        except (AppValidationError, ValueError) as exc:
            logger.warning("导入应用 %s 失败：%s", raw.get("name"), exc)


def create_app(settings: Optional[Settings] = None, registry_transport: Any = None, deploy_transport: Any = None,
               hcs_transport: Any = None) -> FastAPI:
    settings = settings or load_settings()
    ctx = build_context(settings, registry_transport, deploy_transport, hcs_transport)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        ctx.auth.bootstrap()
        ctx.deploy.start()
        await ctx.manager.start()
        _import_configured_apps(ctx)
        ctx.store.ensure_default_project_registry_orgs(settings.registry.organization)
        yield
        await ctx.manager.stop()
        await ctx.events.drain()
        await ctx.deploy.stop()
        await ctx.events.drain()
        await ctx.registry.aclose()

    docs = settings.server.api_docs
    app = FastAPI(title="构建管理平台", version=__version__, lifespan=lifespan,
                  docs_url="/docs" if docs else None, redoc_url="/redoc" if docs else None,
                  openapi_url="/openapi.json" if docs else None)
    app.state.ctx = ctx
    app.state.settings, app.state.store, app.state.manager = settings, ctx.store, ctx.manager

    @app.exception_handler(DomainError)
    async def domain_error(_request: Request, exc: DomainError):
        return JSONResponse({"detail": exc.message, "code": exc.code}, status_code=exc.status)

    @app.exception_handler(AuthError)
    async def auth_error(_request: Request, exc: AuthError):
        code = {401: "AUTH_FAILED", 403: "ACCOUNT_DISABLED", 429: "ACCOUNT_LOCKED"}.get(exc.status, "AUTH_ERROR")
        return JSONResponse({"detail": str(exc), "code": code}, status_code=exc.status)

    @app.exception_handler(ValueError)
    async def validation_error(_request: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc), "code": "BAD_REQUEST"}, status_code=400)

    middleware.install(app, ctx)
    include_routers(app)
    spa.install(app)
    return app
