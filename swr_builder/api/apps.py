"""应用、版本、Dockerfile 预览、构建与构建记录。业务规则见 services/apps.py、services/builds.py。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, File, Form, Request, Response, UploadFile

from ..apps import APP_DEFAULTS
from ..dockerfile import import_dockerfile
from ..errors import DomainError
from ..services import apps as app_service
from ..services import builds as build_service
from ..services.access import app_or_404, job_or_404
from ..web.deps import Ctx, Me, audit, requires

router = APIRouter()


# ------------------------------------------------------------------ 应用
@router.get("/api/app-defaults")
@requires("app.view")
async def app_defaults():
    return APP_DEFAULTS


@router.get("/api/apps")
@requires("app.view")
async def list_apps(ctx: Ctx, me: Me, project_id: Optional[int] = None):
    items = app_service.list_apps(ctx.store, ctx.settings, me, project_id)
    return {"items": items, "total": len(items)}


@router.post("/api/apps", status_code=201)
@requires("app.manage")
async def create_app(request: Request, ctx: Ctx, me: Me, payload: Dict[str, Any] = Body(...)):
    app = app_service.create_app(ctx.store, ctx.settings, me, payload)
    audit(request, "app.create", app["id"], f"name={app['name']};project={app['project_id']}")
    return app


@router.get("/api/apps/{app_id}")
@requires("app.view")
async def get_app(app_id: int, ctx: Ctx, me: Me):
    return app_service.decorate(ctx.settings, app_or_404(ctx.store, me, app_id))


@router.put("/api/apps/{app_id}")
@requires("app.manage")
async def update_app(app_id: int, request: Request, ctx: Ctx, me: Me, payload: Dict[str, Any] = Body(...)):
    existing = app_or_404(ctx.store, me, app_id, "app.manage")
    app = app_service.update_app(ctx.store, ctx.settings, existing, payload)
    audit(request, "app.update", app_id, f"name={app['name']}")
    return app


@router.delete("/api/apps/{app_id}", status_code=204)
@requires("app.manage")
async def delete_app(app_id: int, request: Request, ctx: Ctx, me: Me):
    app = app_service.delete_app(ctx.store, me, app_id)
    audit(request, "app.delete", app_id, f"name={app['name']}")
    return Response(status_code=204)


@router.post("/api/dockerfile/import")
@requires("app.manage")
async def import_dockerfile_api(payload: Dict[str, Any] = Body(...)):
    text = str(payload.get("text") or "")
    if not text.strip():
        raise DomainError("请粘贴 Dockerfile 内容")
    return import_dockerfile(text)


@router.get("/api/apps/{app_id}/versions")
@requires("app.view")
async def versions(app_id: int, ctx: Ctx, me: Me):
    data = app_or_404(ctx.store, me, app_id)
    return await app_service.versions(ctx.store, ctx.settings, ctx.registry, data)


@router.post("/api/apps/{app_id}/preview")
@requires("app.view")
async def preview(app_id: int, ctx: Ctx, me: Me, payload: Dict[str, Any] = Body(default={})):
    data = app_or_404(ctx.store, me, app_id)
    return await app_service.preview(ctx.store, ctx.settings, ctx.registry, data, payload)


# ------------------------------------------------------------------ 构建
@router.post("/api/builds", status_code=201)
@requires("build.run")
async def create_build(request: Request, ctx: Ctx, me: Me, app_id: int = Form(...), mode: str = Form(""),
                       tag: str = Form(""), base_tag: str = Form(""), operator: str = Form(""), remark: str = Form(""),
                       extract_zip: str = Form("false"), files: List[UploadFile] = File(...)):
    """上传文件并创建构建任务。operator 字段为兼容旧客户端保留，实际以登录账号为准。"""
    app = app_or_404(ctx.store, me, app_id, "build.run")
    job_id = await build_service.submit_build(ctx.store, ctx.settings, ctx.manager, me.user, app, mode=mode, tag=tag,
                                              base_tag=base_tag, remark=remark, extract_zip=extract_zip, uploads=files)
    audit(request, "build.submit", job_id, f"app={app_id};mode={mode or app['default_mode']};files={len(files)}")
    return {"id": job_id}


@router.get("/api/jobs")
@requires("job.view")
async def list_jobs(ctx: Ctx, me: Me, app_id: Optional[int] = None, project_id: Optional[int] = None,
                    status: Optional[str] = None, limit: int = 30, offset: int = 0):
    items, total = build_service.list_jobs(ctx.store, me, app_id=app_id, project_id=project_id, status=status or None,
                                           limit=limit, offset=offset)
    return {"items": items, "total": total}


@router.get("/api/jobs/{job_id}")
@requires("job.view")
async def get_job(job_id: int, ctx: Ctx, me: Me):
    return job_or_404(ctx.store, me, job_id)


@router.get("/api/jobs/{job_id}/log")
@requires("job.view")
async def job_log(job_id: int, ctx: Ctx, me: Me, offset: int = 0):
    return build_service.job_log(ctx.store, ctx.manager, me, job_id, offset)


@router.post("/api/jobs/{job_id}/cancel")
@requires("job.cancel.own")
async def cancel_job(job_id: int, request: Request, ctx: Ctx, me: Me):
    await build_service.cancel_job(ctx.store, ctx.manager, me, job_id)
    audit(request, "job.cancel", job_id)
    return {"ok": True}
