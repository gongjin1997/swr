"""服务、配置修订、密钥与服务发布。命名空间作用域权限在这里逐个检查。"""
from __future__ import annotations

import re

from fastapi import APIRouter, Body, Header, Query, Request, Response

from ..authz import has_namespace_permission, has_project_permission, visible_namespace_ids, visible_project_ids
from ..errors import Conflict, Forbidden, NotFound, NotImplementedYet, RevisionConflict, Unprocessable
from ..services import releases
from ..services.access import service_or_404
from ..services.catalog import normalize_service_spec, revision_diff, service_response
from ..services.discovery import service_runtime
from ..validation import DNS_LABEL_RE, reject_unknown, text_field
from ..web.deps import Ctx, User, audit

router = APIRouter()
SECRET_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}")


def _require(ctx, user, service, permission: str, message: str) -> None:
    if not has_namespace_permission(ctx.store, user, service["namespace_id"], permission):
        raise Forbidden(message)


@router.get("/api/services")
async def list_services(ctx: Ctx, user: User, project_id: int = None, namespace_id: int = None, app_id: int = None):
    """项目上有 app.view 或命名空间上有 namespace.view 的服务都可见。"""
    projects = set(visible_project_ids(ctx.store, user, "app.view"))
    namespaces = set(visible_namespace_ids(ctx.store, user, "namespace.view"))
    rows = ctx.store.list_services(sorted(projects), sorted(namespaces))
    rows = [s for s in rows if (project_id is None or s["project_id"] == project_id)
            and (namespace_id is None or s["namespace_id"] == namespace_id)
            and (app_id is None or s["app_id"] == app_id)]
    return {"items": [_linked_service(ctx, row) for row in rows], "total": len(rows)}


def _linked_service(ctx, row):
    ns = ctx.store.get_namespace(row["namespace_id"])
    project = ctx.store.get_project(row["project_id"])
    app = ctx.store.get_app(row["app_id"])
    return dict(service_response(row), project_name=project["name"] if project else "",
                app_name=app["name"] if app else "", cluster_name=ns["cluster_name"] if ns else "",
                namespace_name=ns["name"] if ns else "", tier=ns["tier"] if ns else "")


@router.post("/api/services", status_code=201)
async def create_service(request: Request, ctx: Ctx, user: User, data: dict = Body(...)):
    reject_unknown(data, {"name", "project_id", "namespace_id", "app_id", "spec"}, "服务请求包含未知字段")
    name = text_field(data, "name", 63).lower()
    if not DNS_LABEL_RE.fullmatch(name):
        raise ValueError("服务名称必须是 DNS-1123 label")
    project_id, namespace_id, app_id = data.get("project_id"), data.get("namespace_id"), data.get("app_id")
    if any(type(v) is not int for v in (project_id, namespace_id, app_id)):
        raise ValueError("project_id、namespace_id、app_id 必须是整数")
    namespace, app = ctx.store.get_namespace(namespace_id), ctx.store.get_app(app_id)
    if (not namespace or not app or app["project_id"] != project_id
            or namespace_id not in ctx.store.project_namespace_ids(project_id)):
        raise NotFound("服务所属资源不存在")
    if not has_namespace_permission(ctx.store, user, namespace_id, "service.config"):
        raise Forbidden("没有命名空间服务配置权限")
    spec = normalize_service_spec(data.get("spec"), namespace)
    service = ctx.store.create_service(name, project_id, namespace_id, app_id, spec, user["id"])
    audit(request, "service.create", service["id"], f"project={project_id};namespace={namespace_id};app={app_id}")
    return service_response(service)


@router.get("/api/services/{id}")
async def get_service(id: int, ctx: Ctx, user: User):
    return _linked_service(ctx, service_or_404(ctx.store, user, id))


@router.get("/api/services/{id}/runtime")
async def get_service_runtime(id: int, ctx: Ctx, user: User):
    service = service_or_404(ctx.store, user, id)
    return await service_runtime(ctx, service)


@router.get("/api/services/{id}/builds")
async def service_builds(id: int, ctx: Ctx, user: User, limit: int = 100, offset: int = 0):
    service = service_or_404(ctx.store, user, id)
    if not (has_namespace_permission(ctx.store, user, service["namespace_id"], "service.deploy") or
            has_project_permission(ctx.store, user, service["project_id"], "job.view")):
        raise Forbidden("没有查看此服务可发布构建的权限")
    rows, total = ctx.store.list_jobs(service["app_id"], "success", min(max(limit, 1), 100),
                                      max(offset, 0), [service["project_id"]])
    fields = ("id", "app_id", "project_id", "app_name", "status", "image", "digest", "new_tag", "created_at")
    return {"items": [{key: row.get(key) for key in fields} for row in rows], "total": total}


@router.put("/api/services/{id}")
async def update_service(id: int, request: Request, ctx: Ctx, user: User, data: dict = Body(...)):
    """基于 base_revision 创建新的配置修订（乐观锁：base_revision 不是当前修订时返回 409）。"""
    reject_unknown(data, {"base_revision", "spec"}, "服务更新请求包含未知字段")
    service = service_or_404(ctx.store, user, id, allow_project_view=False)
    _require(ctx, user, service, "service.config", "没有命名空间服务配置权限")
    base_revision = data.get("base_revision")
    if type(base_revision) is not int:
        raise ValueError("base_revision 必须是整数")
    spec = normalize_service_spec(data.get("spec"), ctx.store.get_namespace(service["namespace_id"]))
    try:
        updated, revision = ctx.store.update_service_revision(id, base_revision, spec, user["id"])
    except RuntimeError as exc:
        if str(exc) == "REVISION_CONFLICT":
            raise RevisionConflict() from None
        raise
    audit(request, "service.revision.create", revision["id"], f"service={id}")
    return {"service": service_response(updated), "revision": revision}


@router.delete("/api/services/{id}", status_code=204)
async def delete_service(id: int, request: Request, ctx: Ctx, user: User):
    service = service_or_404(ctx.store, user, id, allow_project_view=False)
    _require(ctx, user, service, "service.delete", "没有服务删除权限")
    if not ctx.store.delete_service(id):
        raise Conflict("存在活动部署，不能删除服务记录")
    audit(request, "service.delete", id)
    return Response(status_code=204)


@router.get("/api/services/{id}/revisions")
async def list_revisions(id: int, ctx: Ctx, user: User):
    service_or_404(ctx.store, user, id)
    rows = ctx.store.list_revisions(id)
    return {"items": rows, "total": len(rows)}


# 注意：/diff 必须注册在 /{revision_id} 之前
@router.get("/api/services/{id}/revisions/diff")
async def diff_revisions(id: int, ctx: Ctx, user: User, from_revision: int = Query(..., alias="from"),
                         to_revision: int = Query(..., alias="to")):
    service_or_404(ctx.store, user, id)
    old, new = ctx.store.get_revision(id, from_revision), ctx.store.get_revision(id, to_revision)
    if not old or not new:
        raise NotFound("服务修订不存在")
    return {"from": from_revision, "to": to_revision, "changes": revision_diff(old["spec"], new["spec"])}


@router.get("/api/services/{id}/revisions/{revision_id}")
async def get_revision(id: int, revision_id: int, ctx: Ctx, user: User):
    service_or_404(ctx.store, user, id)
    revision = ctx.store.get_revision(id, revision_id)
    if not revision:
        raise NotFound("服务修订不存在")
    return revision


@router.post("/api/services/{id}/render")
async def render_service(id: int, ctx: Ctx, user: User, data: dict = Body(...)):
    reject_unknown(data, {"revision_id", "dry_run"}, "渲染请求包含未知字段")
    service = service_or_404(ctx.store, user, id, allow_project_view=False)
    _require(ctx, user, service, "service.config", "没有服务配置权限")
    revision_id, dry_run = data.get("revision_id"), data.get("dry_run", True)
    if type(revision_id) is not int or not isinstance(dry_run, bool):
        raise ValueError("revision_id 或 dry_run 格式错误")
    revision = ctx.store.get_revision(id, revision_id)
    if not revision:
        raise NotFound("服务修订不存在")
    if revision["spec"].get("mode") != "image_only":
        raise NotImplementedYet("managed manifest rendering is not enabled")
    warnings = ["Kubernetes dry-run 尚未执行；清单渲染与现场 API 校验将在部署 worker 阶段完成。"] if dry_run else []
    return {"revision_id": revision_id, "valid": True, "resources": [{"kind": "Deployment", "name": service["name"]}],
            "warnings": warnings}


@router.put("/api/services/{id}/secrets/{key}")
async def update_secret(id: int, key: str, request: Request, ctx: Ctx, user: User, data: dict = Body(...)):
    value = data.get("value")
    if set(data) != {"value"} or not isinstance(value, str) or not value or len(value) > 65536:
        raise ValueError("value 必须是 1–65536 字符的非空字符串")
    if not SECRET_KEY_RE.fullmatch(key):
        raise ValueError("Secret key 格式无效")
    service = service_or_404(ctx.store, user, id, allow_project_view=False)
    _require(ctx, user, service, "secret.manage", "没有服务密钥管理权限")
    revision = ctx.store.create_secret_revision(id, key, ctx.box.encrypt(value), user["id"])
    version = revision["secret_versions"][key]
    audit(request, "service.secret.update", id, f"key={key};version={version}")
    return {"key": key, "version": version, "updated_at": revision["created_at"]}


# ------------------------------------------------------------------ 发布
@router.post("/api/services/{id}/deployments", status_code=202)
async def submit_deployment(id: int, ctx: Ctx, user: User, data: dict = Body(...),
                            idempotency_key: str = Header(..., alias="Idempotency-Key")):
    reject_unknown(data, {"job_id", "revision_id", "use_latest_config", "remark"}, "部署请求包含未知字段")
    releases.check_idempotency_key(idempotency_key)
    service = service_or_404(ctx.store, user, id, allow_project_view=False)
    _require(ctx, user, service, "service.deploy", "没有服务发布权限")
    job_id = data.get("job_id")
    if type(job_id) is not int:
        raise ValueError("job_id 必须是整数")
    job = ctx.store.get_job(job_id)
    if (not job or job["status"] != "success" or job["app_id"] != service["app_id"]
            or job["project_id"] != service["project_id"]):
        raise Unprocessable("构建必须成功且属于服务对应的应用和项目")
    use_latest = data.get("use_latest_config", False)
    if not isinstance(use_latest, bool):
        raise ValueError("use_latest_config 必须是布尔值")
    revision = releases.resolve_revision(ctx.store, service, data.get("revision_id"), use_latest)
    deployment = releases.queue_deployment(ctx.store, service, job, revision, user, "deploy", idempotency_key,
                                           text_field(data, "remark", 200))
    return releases.deployment_response(deployment)


@router.post("/api/services/{id}/rollback", status_code=202)
async def rollback_service(id: int, ctx: Ctx, user: User, data: dict = Body(...),
                           idempotency_key: str = Header(..., alias="Idempotency-Key")):
    reject_unknown(data, {"target_deployment_id", "remark"}, "回滚请求包含未知字段")
    service = service_or_404(ctx.store, user, id, allow_project_view=False)
    _require(ctx, user, service, "service.rollback", "没有服务回滚权限")
    target_id = data.get("target_deployment_id")
    if type(target_id) is not int:
        raise ValueError("target_deployment_id 必须是整数")
    target = ctx.store.get_deployment(target_id)
    if not target or target["service_id"] != id or target["status"] != "success":
        raise NotFound("成功部署记录不存在")
    job, revision = ctx.store.get_job(target["job_id"]), ctx.store.get_revision(id, target["revision_id"])
    if not job or not revision:
        raise Conflict("历史发布引用已不可用")
    deployment = releases.queue_deployment(ctx.store, service, job, revision, user, "rollback", idempotency_key,
                                           text_field(data, "remark", 200))
    return releases.deployment_response(deployment)


@router.post("/api/services/{id}/restart", status_code=202)
async def restart_service(id: int, ctx: Ctx, user: User, data: dict = Body(...),
                          idempotency_key: str = Header(..., alias="Idempotency-Key")):
    """以最近一次成功发布的构建和修订重新发布。"""
    reject_unknown(data, {"remark"}, "重启请求包含未知字段")
    service = service_or_404(ctx.store, user, id, allow_project_view=False)
    _require(ctx, user, service, "service.operate", "没有服务运维权限")
    rows, _ = ctx.store.list_deployments(app_id=service["app_id"], project_ids=[service["project_id"]], limit=100, offset=0)
    previous = next((d for d in rows if d["service_id"] == id and d["status"] == "success"), None)
    if not previous:
        raise Conflict("服务没有成功部署记录")
    job, revision = ctx.store.get_job(previous["job_id"]), ctx.store.get_revision(id, previous["revision_id"])
    deployment = releases.queue_deployment(ctx.store, service, job, revision, user, "restart", idempotency_key,
                                           text_field(data, "remark", 200))
    return releases.deployment_response(deployment)


@router.post("/api/services/{id}/scale", status_code=202)
async def scale_service(id: int, ctx: Ctx, user: User, data: dict = Body(...),
                        idempotency_key: str = Header(..., alias="Idempotency-Key")):
    reject_unknown(data, {"replicas", "base_revision", "remark"}, "扩缩容请求包含未知字段")
    service = service_or_404(ctx.store, user, id, allow_project_view=False)
    _require(ctx, user, service, "service.operate", "没有服务运维权限")
    raise NotImplementedYet("controlled scaling is not enabled until managed workloads are supported")
