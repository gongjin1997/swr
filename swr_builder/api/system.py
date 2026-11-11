"""平台信息、工作台概览、系统设置、审计日志与 HCS 诊断。"""
from __future__ import annotations

import platform
import shutil

from fastapi import APIRouter, Body, Request
from fastapi.responses import Response

from .. import __version__
from ..cloud import HCSClient
from ..errors import UpstreamError
from ..registry import RegistryClient, RegistryError
from ..services.overview import overview as build_overview
from ..web.deps import Ctx, Me, User, audit, authenticated, display_name, requires

router = APIRouter()


def _mask_user(user: str) -> str:
    """SWR 登录名形如 区域_项目@AK：只显示 AK 的首尾 4 位。"""
    if "@" in user:
        prefix, ak = user.rsplit("@", 1)
        if len(ak) > 8:
            ak = ak[:4] + "****" + ak[-4:]
        return f"{prefix}@{ak}"
    return user


@router.get("/healthz", include_in_schema=False)
async def healthz():
    return {"ok": True}


@router.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)


@router.get("/api/info")
@authenticated
async def info(ctx: Ctx, user: User):
    settings = ctx.settings
    username, password = settings.registry.credentials()
    return {
        "version": __version__,
        "registry": {"address": settings.registry.host, "organization": settings.registry.organization,
                     "username": _mask_user(username), "configured": bool(settings.registry.host and password)},
        "docker": await ctx.docker.get(),
        "user": display_name(user),
        "max_upload_mb": settings.build.max_upload_mb,
    }


@router.get("/api/overview")
@authenticated
async def overview(ctx: Ctx, me: Me):
    """工作台概览：可见范围内的项目、应用、服务、构建统计与待我审批的发布。"""
    return build_overview(ctx.store, me)


async def _ping(client: RegistryClient, message: str):
    try:
        await client.ping()
    except RegistryError as exc:
        raise UpstreamError(str(exc)) from None
    return {"ok": True, "message": message}


@router.post("/api/registry/test")
@requires("settings.manage")
async def registry_test_current(ctx: Ctx):
    """用当前生效的仓库配置测试连接；失败返回 502 和原因。"""
    return await _ping(ctx.registry, "连接成功")


@router.get("/api/settings")
@requires("settings.manage")
async def settings_view(ctx: Ctx):
    return ctx.sysconfig.view()


@router.put("/api/settings")
@requires("settings.manage")
async def settings_update(request: Request, ctx: Ctx, user: User, data: dict = Body(...)):
    values, clear = data.get("values", {}), data.get("clear", [])
    if not isinstance(values, dict) or not isinstance(clear, list) or not all(isinstance(p, str) for p in clear):
        raise ValueError("设置格式错误")
    paths = set(values) | set(clear)
    if ctx.manager.has_running_jobs() and any(p.startswith(("registry.", "build.")) for p in paths):
        raise ValueError("存在未结束的构建，请等待后再修改仓库或构建设置")
    changed = ctx.sysconfig.update(values, clear, user["username"])
    audit(request, "settings.update", detail=",".join(changed))
    return {"ok": True, "changed": changed}


@router.post("/api/settings/registry/test")
@requires("settings.manage")
async def registry_test_candidate(ctx: Ctx, data: dict = Body(default={})):
    """用表单中尚未保存的仓库配置测试连接。"""
    cfg = ctx.sysconfig.candidate_registry(data.get("values", {}), data.get("clear", []))
    client = RegistryClient(cfg, transport=ctx.registry_transport)
    try:
        return await _ping(client, "连接成功（仅验证 Registry API；不验证 Docker 推送权限）")
    finally:
        await client.aclose()


@router.get("/api/audit")
@requires("audit.view")
async def audit_log(ctx: Ctx, limit: int = 50, offset: int = 0, username: str = "", action: str = ""):
    items, total = ctx.store.list_audit(min(max(limit, 1), 100), max(offset, 0), username, action)
    return {"items": items, "total": total}


@router.get("/api/system")
@requires("settings.manage")
async def system(ctx: Ctx):
    data_path = ctx.settings.data_path
    disk = shutil.disk_usage(data_path)
    return {"version": __version__, "python": platform.python_version(), "data_dir": str(data_path),
            "disk": {"total": disk.total, "free": disk.free}, "users": ctx.store.count_users(),
            "jobs": ctx.store.count_jobs(), "config_priority": "环境变量 > 页面设置（数据库） > config.yaml"}


@router.post("/api/cloud/hcs/test")
@requires("settings.manage")
async def hcs_test(ctx: Ctx, data: dict = Body(default={})):
    cfg = ctx.sysconfig.candidate_hcs(data.get("values", {}), data.get("clear", []))
    client = HCSClient(cfg, transport=ctx.hcs_transport)
    try:
        return await client.test()
    finally:
        await client.close()


@router.get("/api/cloud/hcs/capabilities")
@requires("settings.manage")
async def hcs_capabilities(ctx: Ctx):
    hcs = ctx.settings.hcs
    return {"platform": "Huawei Cloud Stack", "version": hcs.version, "enabled": hcs.enabled,
            "capabilities": [
                {"name": "IAM AK/SK 签名换 Token / 密码 / 预置 Token", "state": "implemented", "live_verified": False},
                {"name": "SWR Registry V2 镜像构建与推送", "state": "implemented", "live_verified": False},
                {"name": "Kubernetes Deployment 更新（直连集群）", "state": "implemented", "live_verified": False},
                {"name": "SWR / CCE / ECS 管理 API", "state": "reserved", "live_verified": False},
                {"name": "ManageOne SSO、审批和自动回滚", "state": "reserved", "live_verified": False}],
            "notice": "尚未连接用户的 Stack 环境；implemented 表示已有代码及模拟测试，不是生产兼容性认证。"}
