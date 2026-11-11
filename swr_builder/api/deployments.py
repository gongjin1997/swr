"""旧部署目标（只读迁移核对）、发布记录、审批与发布日志。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Request

from ..access import Principal
from ..deploy import list_providers
from ..errors import DomainError, NotFound
from ..fileio import read_log
from ..services import releases
from ..store import Store
from ..validation import reject_unknown, text_field
from ..web.deps import Ctx, Me, audit, authenticated, requires

router = APIRouter()
FINAL_DEPLOYMENT_STATUSES = ("success", "failed", "cancelled", "rejected", "expired", "unknown")


# ------------------------------------------------------------------ 旧部署目标
@router.get("/api/deploy/providers")
@requires("settings.manage")
async def providers():
    return list_providers()


@router.get("/api/deploy/targets")
@requires("deploy.view")
async def list_targets(ctx: Ctx, me: Me):
    manage = me.can("deploy.manage")
    return [ctx.deploy.view(t, manage) for t in ctx.store.list_deploy_targets()]


@router.post("/api/deploy/targets", status_code=201)
@requires("deploy.manage")
async def create_target(ctx: Ctx, data: dict = Body(...)):
    return ctx.deploy.view(ctx.deploy.save_target(data), True)


@router.put("/api/deploy/targets/{id}")
@requires("deploy.manage")
async def update_target(id: int, ctx: Ctx, data: dict = Body(...)):
    return ctx.deploy.view(ctx.deploy.save_target(data, id), True)


@router.delete("/api/deploy/targets/{id}")
@requires("deploy.manage")
async def delete_target(id: int, ctx: Ctx):
    if not ctx.store.delete_deploy_target(id):
        raise NotFound("目标不存在")
    return {"ok": True}


@router.post("/api/deploy/targets/{id}/test")
@requires("deploy.manage")
async def test_target(id: int, ctx: Ctx):
    return {"ok": True, "message": await ctx.deploy.test_target(id)}


@router.post("/api/deployments", status_code=201)
@requires("deploy.run")
async def create_legacy_deployment(request: Request, data: dict = Body(...)):
    audit(request, "deploy.legacy_blocked", detail="migrate this target to a scoped service revision")
    raise DomainError("旧部署目标不再执行发布。请在资源管理创建服务并通过 namespace 授权与审批队列发布。",
                      status=410, code="LEGACY_DEPLOY_REMOVED")


# ------------------------------------------------------------------ 审批
async def _decide(id: int, request: Request, ctx, me: Principal, data: dict, decision: str):
    reject_unknown(data, {"comment"}, "审批请求包含未知字段")
    record = ctx.store.get_deployment(id)
    if not record or not record.get("service_id") or not ctx.store.get_service(record["service_id"]):
        raise NotFound("发布任务不存在")
    admin_required = decision == "approved" and releases.requires_admin_approval(ctx.store, record)
    allowed = me.is_platform_admin if admin_required else me.can_namespace(record["namespace_id"], "deploy.approve")
    if not allowed:
        raise NotFound("发布任务不存在")
    result = releases.decide(ctx.store, record, me, decision, text_field(data, "comment", 500))
    audit(request, "deploy." + decision, id)
    return result


@router.post("/api/deployments/{id}/approve")
@authenticated
async def approve(id: int, request: Request, ctx: Ctx, me: Me, data: dict = Body(...)):
    return await _decide(id, request, ctx, me, data, "approved")


@router.post("/api/deployments/{id}/reject")
@authenticated
async def reject(id: int, request: Request, ctx: Ctx, me: Me, data: dict = Body(...)):
    return await _decide(id, request, ctx, me, data, "rejected")


# ------------------------------------------------------------------ 发布记录
class _Lookup:
    """补充名称与审批能力时用到的项目、命名空间，每个 ID 只查询一次。"""

    def __init__(self, store: Store):
        self.store: Store = store
        self.projects: Dict[Any, Optional[Dict[str, Any]]] = {}
        self.namespaces: Dict[Any, Optional[Dict[str, Any]]] = {}

    def project(self, project_id):
        if project_id not in self.projects:
            self.projects[project_id] = self.store.get_project(project_id) if project_id else None
        return self.projects[project_id]

    def namespace(self, namespace_id):
        if namespace_id not in self.namespaces:
            self.namespaces[namespace_id] = self.store.get_namespace(namespace_id) if namespace_id else None
        return self.namespaces[namespace_id]


def _enrich(store: Store, me: Principal, record: Dict[str, Any], lookup: _Lookup) -> Dict[str, Any]:
    ns = lookup.namespace(record.get("namespace_id"))
    project = lookup.project(record.get("project_id"))
    pending = record["status"] == "pending_approval"
    admin_required = releases.requires_admin_approval(store, record, ns) if ns else False
    record.update(project_name=project["name"] if project else "",
                  namespace_name=ns["name"] if ns else "", cluster_name=ns["cluster_name"] if ns else "",
                  can_approve=pending and releases.can_decide(store, me, record, admin_required=admin_required),
                  can_reject=pending and releases.can_decide(store, me, record, "rejected"),
                  require_admin_approval=admin_required)
    return record


@router.get("/api/deployments")
@authenticated
async def list_deployments(ctx: Ctx, me: Me, target_id: Optional[int] = None, app_id: Optional[int] = None,
                           limit: int = 30, offset: int = 0, project_id: Optional[int] = None,
                           namespace_id: Optional[int] = None, service_id: Optional[int] = None,
                           status: Optional[str] = None):
    deploy_projects, app_projects = me.project_ids("deploy.view"), me.project_ids("app.view")
    namespaces = me.namespace_ids("namespace.view")
    if deploy_projects is None or app_projects is None or namespaces is None:
        projects = namespaces = None  # 全局可见
    else:
        projects = sorted(set(deploy_projects) | set(app_projects))
    items, total = ctx.store.list_deployments(target_id, app_id, min(max(limit, 1), 100), max(offset, 0),
                                              projects, namespaces, project_id=project_id, namespace_id=namespace_id,
                                              service_id=service_id, status=status)
    lookup = _Lookup(ctx.store)
    return {"items": [_enrich(ctx.store, me, item, lookup) for item in items], "total": total}


def _visible_deployment(ctx, me: Principal, id: int):
    record = ctx.store.get_deployment(id)
    if record and (me.can_project(record.get("project_id"), "deploy.view")
                   or (record.get("service_id") and (me.can_project(record["project_id"], "app.view")
                                                     or me.can_namespace(record.get("namespace_id"), "namespace.view")))):
        return record
    raise NotFound("部署记录不存在")


@router.get("/api/deployments/{id}")
@authenticated
async def get_deployment(id: int, ctx: Ctx, me: Me):
    record = _visible_deployment(ctx, me, id)
    record["approvals"] = ctx.store.list_approvals(id) if record.get("service_id") else []
    return _enrich(ctx.store, me, record, _Lookup(ctx.store))


@router.get("/api/deployments/{id}/log")
@authenticated
async def deployment_log(id: int, ctx: Ctx, me: Me, offset: int = 0):
    record = _visible_deployment(ctx, me, id)
    text, new_offset, size = read_log(ctx.deploy.log_path(id), offset)
    return {"text": text, "offset": new_offset, "status": record["status"],
            "finished": record["status"] in FINAL_DEPLOYMENT_STATUSES and new_offset >= size}
