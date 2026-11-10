"""旧部署目标（只读迁移核对）、发布记录、审批与发布日志。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Body, Request

from ..auth import has_perm
from ..authz import has_global_permission, has_namespace_permission, has_project_permission, visible_namespace_ids, visible_project_ids
from ..deploy import list_providers
from ..errors import DomainError, NotFound
from ..fileio import read_log
from ..services import releases
from ..validation import reject_unknown, text_field
from ..web.deps import Ctx, User, audit

router = APIRouter()
FINAL_DEPLOYMENT_STATUSES = ("success", "failed", "cancelled", "rejected", "expired", "unknown")


# ------------------------------------------------------------------ 旧部署目标
@router.get("/api/deploy/providers")
async def providers():
    return list_providers()


@router.get("/api/deploy/targets")
async def list_targets(ctx: Ctx, user: User):
    manage = has_perm(user["role"], "deploy.manage")
    return [ctx.deploy.view(t, manage) for t in ctx.store.list_deploy_targets()]


@router.post("/api/deploy/targets", status_code=201)
async def create_target(ctx: Ctx, data: dict = Body(...)):
    return ctx.deploy.view(ctx.deploy.save_target(data), True)


@router.put("/api/deploy/targets/{id}")
async def update_target(id: int, ctx: Ctx, data: dict = Body(...)):
    return ctx.deploy.view(ctx.deploy.save_target(data, id), True)


@router.delete("/api/deploy/targets/{id}")
async def delete_target(id: int, ctx: Ctx):
    if not ctx.store.delete_deploy_target(id):
        raise NotFound("目标不存在")
    return {"ok": True}


@router.post("/api/deploy/targets/{id}/test")
async def test_target(id: int, ctx: Ctx):
    return {"ok": True, "message": await ctx.deploy.test_target(id)}


@router.post("/api/deployments", status_code=201)
async def create_legacy_deployment(request: Request, data: dict = Body(...)):
    audit(request, "deploy.legacy_blocked", detail="migrate this target to a scoped service revision")
    raise DomainError("旧部署目标不再执行发布。请在资源管理创建服务并通过 namespace 授权与审批队列发布。",
                      status=410, code="LEGACY_DEPLOY_REMOVED")


# ------------------------------------------------------------------ 审批
async def _decide(id: int, request: Request, ctx, user, data: dict, decision: str):
    reject_unknown(data, {"comment"}, "审批请求包含未知字段")
    record = ctx.store.get_deployment(id)
    if not record or not record.get("service_id"):
        raise NotFound("发布任务不存在")
    service = ctx.store.get_service(record["service_id"])
    if not service:
        raise NotFound("发布任务不存在")
    permission = "platform.admin" if decision == "approved" and releases.requires_admin_approval(ctx.store, record) else "deploy.approve"
    if not (has_global_permission(ctx.store, user, permission) or
            (permission == "deploy.approve" and has_namespace_permission(ctx.store, user, record["namespace_id"], permission))):
        raise NotFound("发布任务不存在")
    result = releases.decide(ctx.store, record, user, decision, text_field(data, "comment", 500))
    audit(request, "deploy." + decision, id)
    return result


@router.post("/api/deployments/{id}/approve")
async def approve(id: int, request: Request, ctx: Ctx, user: User, data: dict = Body(...)):
    return await _decide(id, request, ctx, user, data, "approved")


@router.post("/api/deployments/{id}/reject")
async def reject(id: int, request: Request, ctx: Ctx, user: User, data: dict = Body(...)):
    return await _decide(id, request, ctx, user, data, "rejected")


# ------------------------------------------------------------------ 发布记录
@router.get("/api/deployments")
async def list_deployments(ctx: Ctx, user: User, target_id: int = None, app_id: int = None, limit: int = 30,
                           offset: int = 0, project_id: int = None, namespace_id: int = None,
                           service_id: int = None, status: str = None):
    projects = sorted(set(visible_project_ids(ctx.store, user, "deploy.view")) |
                      set(visible_project_ids(ctx.store, user, "app.view")))
    namespaces = visible_namespace_ids(ctx.store, user, "namespace.view")
    items, total = ctx.store.list_deployments(target_id, app_id, min(max(limit, 1), 100), max(offset, 0),
                                              projects, namespaces, project_id=project_id, namespace_id=namespace_id,
                                              service_id=service_id, status=status)
    for item in items:
        _enrich(ctx, user, item)
    return {"items": items, "total": total}


def _enrich(ctx, user, record):
    ns = ctx.store.get_namespace(record.get("namespace_id")) if record.get("namespace_id") else None
    project = ctx.store.get_project(record.get("project_id"))
    record.update(project_name=project["name"] if project else "",
                  namespace_name=ns["name"] if ns else "", cluster_name=ns["cluster_name"] if ns else "",
                  can_approve=record["status"] == "pending_approval" and releases.can_decide(ctx.store, user, record),
                  can_reject=record["status"] == "pending_approval" and releases.can_decide(ctx.store, user, record, "rejected"),
                  require_admin_approval=releases.requires_admin_approval(ctx.store, record) if ns else False)
    return record


def _visible_deployment(ctx, user, id: int):
    record = ctx.store.get_deployment(id)
    if record and has_project_permission(ctx.store, user, record.get("project_id") or 0, "deploy.view"):
        return record
    if (record and record.get("service_id") and
            has_project_permission(ctx.store, user, record["project_id"], "app.view")):
        return record
    if (record and record.get("service_id")
            and has_namespace_permission(ctx.store, user, record.get("namespace_id") or 0, "namespace.view")):
        return record
    raise NotFound("部署记录不存在")


@router.get("/api/deployments/{id}")
async def get_deployment(id: int, ctx: Ctx, user: User):
    record = _visible_deployment(ctx, user, id)
    record["approvals"] = ctx.store.list_approvals(id) if record.get("service_id") else []
    return _enrich(ctx, user, record)


@router.get("/api/deployments/{id}/log")
async def deployment_log(id: int, ctx: Ctx, user: User, offset: int = 0):
    record = _visible_deployment(ctx, user, id)
    text, new_offset, size = read_log(ctx.deploy.log_path(id), offset)
    return {"text": text, "offset": new_offset, "status": record["status"],
            "finished": record["status"] in FINAL_DEPLOYMENT_STATUSES and new_offset >= size}
