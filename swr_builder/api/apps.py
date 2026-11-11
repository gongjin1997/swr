"""应用、版本、Dockerfile 预览、构建与构建记录。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, File, Form, UploadFile

from ..access import same_user
from ..apps import APP_DEFAULTS
from ..builder import FINAL_STATUSES
from ..dockerfile import import_dockerfile
from ..errors import Conflict, DomainError, Forbidden, NotFound
from ..fileio import read_log
from ..services import apps as app_service
from ..services.access import app_or_404, job_or_404
from ..services.builds import submit_build
from ..web.deps import Ctx, Me, requires

router = APIRouter()


# ------------------------------------------------------------------ 应用
@router.get("/api/app-defaults")
@requires("app.view")
async def app_defaults():
    return APP_DEFAULTS


@router.get("/api/apps")
@requires("app.view")
async def list_apps(ctx: Ctx, me: Me):
    return [app_service.decorate(ctx.settings, a) for a in ctx.store.list_apps(me.project_ids("app.view"))]


@router.post("/api/apps", status_code=201)
@requires("app.manage")
async def create_app(ctx: Ctx, me: Me, payload: Dict[str, Any] = Body(...)):
    return app_service.create_app(ctx.store, ctx.settings, me, payload)


@router.put("/api/apps/{app_id}")
@requires("app.manage")
async def update_app(app_id: int, ctx: Ctx, me: Me, payload: Dict[str, Any] = Body(...)):
    existing = app_or_404(ctx.store, me, app_id, "app.manage")
    return app_service.update_app(ctx.store, ctx.settings, existing, payload)


@router.delete("/api/apps/{app_id}")
@requires("app.manage")
async def delete_app(app_id: int, ctx: Ctx, me: Me):
    app_or_404(ctx.store, me, app_id, "app.manage")
    if any(s["app_id"] == app_id for s in ctx.store.list_services()):
        raise Conflict("应用仍有关联服务，请先处理服务关联")
    if not ctx.store.delete_app(app_id):
        raise NotFound("应用不存在")
    return {"ok": True}


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
async def create_build(ctx: Ctx, me: Me, app_id: int = Form(...), mode: str = Form(""), tag: str = Form(""),
                       base_tag: str = Form(""), operator: str = Form(""), remark: str = Form(""),
                       extract_zip: str = Form("false"), files: List[UploadFile] = File(...)):
    """上传文件并创建构建任务。operator 字段为兼容旧客户端保留，实际以登录账号为准。"""
    app = app_or_404(ctx.store, me, app_id, "build.run")
    job_id = await submit_build(ctx.store, ctx.settings, ctx.manager, me.user, app, mode=mode, tag=tag,
                                base_tag=base_tag, remark=remark, extract_zip=extract_zip, uploads=files)
    return {"id": job_id}


@router.get("/api/jobs")
@requires("job.view")
async def list_jobs(ctx: Ctx, me: Me, app_id: Optional[int] = None, status: Optional[str] = None,
                    limit: int = 30, offset: int = 0):
    projects = me.project_ids("job.view")
    if app_id is not None:
        app = app_or_404(ctx.store, me, app_id)
        projects = [app["project_id"]] if me.can_project(app["project_id"], "job.view") else []
    items, total = ctx.store.list_jobs(app_id, status or None, min(max(limit, 1), 100), max(offset, 0), projects)
    return {"items": items, "total": total}


@router.get("/api/jobs/{job_id}")
@requires("job.view")
async def get_job(job_id: int, ctx: Ctx, me: Me):
    return job_or_404(ctx.store, me, job_id)


@router.get("/api/jobs/{job_id}/log")
@requires("job.view")
async def job_log(job_id: int, ctx: Ctx, me: Me, offset: int = 0):
    job = job_or_404(ctx.store, me, job_id)
    text, new_offset, size = read_log(ctx.manager.log_path(job_id), offset)
    return {"text": text, "offset": new_offset, "status": job["status"], "step": job["step"],
            "finished": job["status"] in FINAL_STATUSES and new_offset >= size}


@router.post("/api/jobs/{job_id}/cancel")
@requires("job.cancel.own")
async def cancel_job(job_id: int, ctx: Ctx, me: Me):
    """项目上有 job.cancel 可取消任何任务；只有 job.cancel.own 时只能取消自己提交的任务。"""
    job = job_or_404(ctx.store, me, job_id)
    project_admin = me.can_project(job["project_id"], "job.cancel")
    own_job = me.can_project(job["project_id"], "job.cancel.own") and same_user(job.get("created_by"), me.id)
    if not project_admin and not own_job:
        raise Forbidden("只能取消自己提交的构建")
    if not await ctx.manager.cancel(job_id):
        raise DomainError("任务已经结束，无法取消")
    return {"ok": True}
