"""发布记录、审批与发布日志；旧部署目标（只读迁移核对与连接测试）。业务规则见 services/releases.py。

本文件的路由都属于部署模块（@deploy_module）：关闭“启用部署中心”时一律拒绝。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Request, Response

from ..deploy import list_providers
from ..errors import DomainError, NotFound
from ..fileio import read_log
from ..services import releases
from ..web.deps import Ctx, Me, audit, authenticated, deploy_module, requires

router = APIRouter()


# ------------------------------------------------------------------ 旧部署目标
@router.get("/api/deploy/providers")
@requires("settings.manage")
@deploy_module
async def providers():
    items = list_providers()
    return {"items": items, "total": len(items)}


@router.get("/api/deploy/targets")
@requires("deploy.view")
@deploy_module
async def list_targets(ctx: Ctx, me: Me):
    manage = me.can("deploy.manage")
    items = [ctx.deploy.view(t, manage) for t in ctx.store.list_deploy_targets()]
    return {"items": items, "total": len(items)}


@router.post("/api/deploy/targets", status_code=201)
@requires("deploy.manage")
@deploy_module
async def create_target(request: Request, ctx: Ctx, data: dict = Body(...)):
    target = ctx.deploy.view(ctx.deploy.save_target(data), True)
    audit(request, "deploy.target.create", target["id"], f"name={target['name']};type={target['type']}")
    return target


@router.put("/api/deploy/targets/{id}")
@requires("deploy.manage")
@deploy_module
async def update_target(id: int, request: Request, ctx: Ctx, data: dict = Body(...)):
    target = ctx.deploy.view(ctx.deploy.save_target(data, id), True)
    audit(request, "deploy.target.update", id, f"name={target['name']}")
    return target


@router.delete("/api/deploy/targets/{id}", status_code=204)
@requires("deploy.manage")
@deploy_module
async def delete_target(id: int, request: Request, ctx: Ctx):
    if not ctx.store.delete_deploy_target(id):
        raise NotFound("目标不存在")
    audit(request, "deploy.target.delete", id)
    return Response(status_code=204)


@router.post("/api/deploy/targets/{id}/test")
@requires("deploy.manage")
@deploy_module
async def test_target(id: int, request: Request, ctx: Ctx):
    message = await ctx.deploy.test_target(id)
    audit(request, "deploy.target.test", id)
    return {"ok": True, "message": message}


@router.post("/api/deployments", status_code=201)
@requires("deploy.run")
@deploy_module
async def create_legacy_deployment(request: Request, data: dict = Body(...)):
    audit(request, "deploy.legacy_blocked", detail="migrate this target to a scoped service revision")
    raise DomainError("旧部署目标不再执行发布。请在资源管理创建服务并通过 namespace 授权与审批队列发布。",
                      status=410, code="LEGACY_DEPLOY_REMOVED")


# ------------------------------------------------------------------ 审批
@router.post("/api/deployments/{id}/approve")
@authenticated
@deploy_module
async def approve(id: int, request: Request, ctx: Ctx, me: Me, data: dict = Body(default={})):
    result = releases.decide_request(ctx.store, me, id, "approved", data)
    audit(request, "deploy.approved", id, f"status={result['status']}")
    return result


@router.post("/api/deployments/{id}/reject")
@authenticated
@deploy_module
async def reject(id: int, request: Request, ctx: Ctx, me: Me, data: dict = Body(default={})):
    result = releases.decide_request(ctx.store, me, id, "rejected", data)
    audit(request, "deploy.rejected", id)
    return result


# ------------------------------------------------------------------ 发布记录
@router.get("/api/deployments")
@authenticated
@deploy_module
async def list_deployments(ctx: Ctx, me: Me, target_id: Optional[int] = None, app_id: Optional[int] = None,
                           limit: int = 30, offset: int = 0, project_id: Optional[int] = None,
                           namespace_id: Optional[int] = None, service_id: Optional[int] = None,
                           status: Optional[str] = None):
    items, total = releases.list_records(ctx.store, me, limit=min(max(limit, 1), 100), offset=max(offset, 0),
                                         target_id=target_id, app_id=app_id, project_id=project_id,
                                         namespace_id=namespace_id, service_id=service_id, status=status or None)
    return {"items": items, "total": total}


@router.get("/api/deployments/{id}")
@authenticated
@deploy_module
async def get_deployment(id: int, ctx: Ctx, me: Me):
    return releases.record_detail(ctx.store, me, id)


@router.get("/api/deployments/{id}/log")
@authenticated
@deploy_module
async def deployment_log(id: int, ctx: Ctx, me: Me, offset: int = 0):
    record = releases.visible_record(ctx.store, me, id)
    text, new_offset, size = read_log(ctx.deploy.log_path(id), offset)
    return {"text": text, "offset": new_offset, "status": record["status"],
            "finished": record["status"] in releases.FINAL_STATUSES and new_offset >= size}
