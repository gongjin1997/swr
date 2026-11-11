"""服务、配置修订、密钥与服务发布。业务规则见 services/catalog.py、services/releases.py。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Header, Query, Request, Response

from ..delivery.spec import DEFAULT_SPEC, TEMPLATES
from ..errors import NotFound, NotImplementedYet
from ..services import catalog, manifests, releases
from ..services.access import service_or_404
from ..services.discovery import service_runtime, services_status
from ..web.deps import Ctx, Me, audit, authenticated

router = APIRouter()


@router.get("/api/services")
@authenticated
async def list_services(ctx: Ctx, me: Me, project_id: Optional[int] = None, namespace_id: Optional[int] = None,
                        app_id: Optional[int] = None):
    """项目上有 app.view 或命名空间上有 namespace.view 的服务都可见。"""
    items = catalog.list_services(ctx.store, me, project_id=project_id, namespace_id=namespace_id, app_id=app_id)
    return {"items": items, "total": len(items)}


@router.get("/api/service-templates")
@authenticated
async def service_templates():
    """内置的服务模板与托管服务规格的默认值（新建向导使用）。"""
    items = [dict(t, defaults=DEFAULT_SPEC) for t in TEMPLATES.values()]
    return {"items": items, "total": len(items)}


@router.post("/api/services", status_code=201)
@authenticated
async def create_service(request: Request, ctx: Ctx, me: Me, data: dict = Body(...)):
    service = catalog.create_service(ctx.store, me, data, ctx.box)
    audit(request, "service.create", service["id"],
          f"project={service['project_id']};namespace={service['namespace_id']};app={service['app_id']};"
          f"mode={service['mode']}")
    return service


@router.post("/api/services/preview")
@authenticated
async def preview_new_service(ctx: Ctx, me: Me, data: dict = Body(...)):
    """新建托管服务前预览清单；dry_run=true 时在集群中做服务端校验。"""
    return await manifests.preview_new(ctx, me, data)


@router.post("/api/services/status")
@authenticated
async def service_status(ctx: Ctx, me: Me, data: dict = Body(...)):
    """服务列表的运行状态（每个命名空间读取一次 Deployment 列表）。只返回当前账号可见的服务。"""
    ids = data.get("ids")
    if not isinstance(ids, list) or len(ids) > 200 or any(type(i) is not int for i in ids):
        raise ValueError("ids 必须是最多 200 个服务 ID 的数组")
    visible = []
    for service_id in dict.fromkeys(ids):
        try:
            visible.append(service_or_404(ctx.store, me, service_id))
        except NotFound:  # 看不到的服务直接忽略
            continue
    items = await services_status(ctx, visible)
    return {"items": items, "total": len(items)}


@router.get("/api/services/{id}")
@authenticated
async def get_service(id: int, ctx: Ctx, me: Me):
    return catalog.get_service(ctx.store, me, id)


@router.get("/api/services/{id}/runtime")
@authenticated
async def get_service_runtime(id: int, ctx: Ctx, me: Me):
    return await service_runtime(ctx, service_or_404(ctx.store, me, id))


@router.get("/api/services/{id}/builds")
@authenticated
async def service_builds(id: int, ctx: Ctx, me: Me, limit: int = 100, offset: int = 0):
    items, total = catalog.deployable_builds(ctx.store, me, id, min(max(limit, 1), 100), max(offset, 0))
    return {"items": items, "total": total}


@router.put("/api/services/{id}")
@authenticated
async def update_service(id: int, request: Request, ctx: Ctx, me: Me, data: dict = Body(...)):
    """基于 base_revision 创建新的配置修订（乐观锁：base_revision 不是当前修订时返回 409）。"""
    service, revision = catalog.update_service_spec(ctx.store, me, id, data)
    audit(request, "service.revision.create", revision["id"], f"service={id};number={revision['number']}")
    return {"service": service, "revision": revision}


@router.delete("/api/services/{id}", status_code=204)
@authenticated
async def delete_service(id: int, request: Request, ctx: Ctx, me: Me):
    catalog.delete_service(ctx.store, me, id)
    audit(request, "service.delete", id)
    return Response(status_code=204)


@router.get("/api/services/{id}/revisions")
@authenticated
async def list_revisions(id: int, ctx: Ctx, me: Me):
    rows = catalog.list_revisions(ctx.store, me, id)
    return {"items": rows, "total": len(rows)}


# 注意：/diff 必须注册在 /{revision_id} 之前
@router.get("/api/services/{id}/revisions/diff")
@authenticated
async def diff_revisions(id: int, ctx: Ctx, me: Me, from_revision: int = Query(..., alias="from"),
                         to_revision: int = Query(..., alias="to")):
    return catalog.diff(ctx.store, me, id, from_revision, to_revision)


@router.get("/api/services/{id}/revisions/{revision_id}")
@authenticated
async def get_revision(id: int, revision_id: int, ctx: Ctx, me: Me):
    return catalog.get_revision(ctx.store, me, id, revision_id)


@router.post("/api/services/{id}/render")
@authenticated
async def render_service(id: int, ctx: Ctx, me: Me, data: dict = Body(...)):
    """托管服务的清单（敏感值已隐藏）；dry_run=true 时在集群中做服务端校验并列出与线上的差异。"""
    return await manifests.preview(ctx, me, id, data)


@router.get("/api/services/{id}/manifest")
@authenticated
async def service_manifest(id: int, ctx: Ctx, me: Me, deployment_id: Optional[int] = None):
    """某次发布（默认最近一次成功发布）的渲染快照。"""
    return manifests.manifest(ctx, me, id, deployment_id)


@router.get("/api/services/{id}/effective-config")
@authenticated
async def service_effective_config(id: int, ctx: Ctx, me: Me):
    """平台 → 项目 → 环境 → 服务合并后的配置，标注每个键来自哪一层。"""
    return catalog.effective_config(ctx.store, me, id)


@router.put("/api/services/{id}/secrets/{key}")
@authenticated
async def update_secret(id: int, key: str, request: Request, ctx: Ctx, me: Me, data: dict = Body(...)):
    result = catalog.set_secret(ctx.store, ctx.box, me, id, key, data)
    audit(request, "service.secret.update", id, f"key={key};version={result['version']}")
    return result


# ------------------------------------------------------------------ 发布
async def _submit(kind: str, id: int, request: Request, ctx, me, data: dict, idempotency_key: str):
    deployment = releases.submit(ctx.store, me, id, kind, data, idempotency_key)
    audit(request, "deploy.submit", deployment["id"],
          f"service={id};kind={kind};status={deployment['status']};revision={deployment['revision_id']}")
    return releases.deployment_response(deployment)


@router.post("/api/services/{id}/deployments", status_code=202)
@authenticated
async def submit_deployment(id: int, request: Request, ctx: Ctx, me: Me, data: dict = Body(...),
                            idempotency_key: str = Header(..., alias="Idempotency-Key")):
    return await _submit("deploy", id, request, ctx, me, data, idempotency_key)


@router.post("/api/services/{id}/rollback", status_code=202)
@authenticated
async def rollback_service(id: int, request: Request, ctx: Ctx, me: Me, data: dict = Body(...),
                           idempotency_key: str = Header(..., alias="Idempotency-Key")):
    return await _submit("rollback", id, request, ctx, me, data, idempotency_key)


@router.post("/api/services/{id}/restart", status_code=202)
@authenticated
async def restart_service(id: int, request: Request, ctx: Ctx, me: Me, data: dict = Body(...),
                          idempotency_key: str = Header(..., alias="Idempotency-Key")):
    """以最近一次成功发布的构建和修订重新发布。"""
    return await _submit("restart", id, request, ctx, me, data, idempotency_key)


@router.post("/api/services/{id}/adopt", status_code=202)
@authenticated
async def adopt_service(id: int, request: Request, ctx: Ctx, me: Me, data: dict = Body(...),
                        idempotency_key: str = Header(..., alias="Idempotency-Key")):
    """接管发布：导入（或由纳管转为托管）的服务第一次由平台下发，镜像沿用集群中正在运行的 digest。"""
    service = catalog.writable_service(ctx.store, me, id, "service.deploy", "没有服务发布权限")
    image = await manifests.live_image(ctx, service)
    deployment = releases.submit(ctx.store, me, id, "adopt", data, idempotency_key, live_image=image)
    audit(request, "deploy.submit", deployment["id"],
          f"service={id};kind=adopt;status={deployment['status']};revision={deployment['revision_id']};"
          f"image={image['image']}")
    return releases.deployment_response(deployment)


@router.post("/api/services/{id}/scale", status_code=202)
@authenticated
async def scale_service(id: int, ctx: Ctx, me: Me, data: dict = Body(...),
                        idempotency_key: str = Header(..., alias="Idempotency-Key")):
    catalog.writable_service(ctx.store, me, id, "service.operate", "没有服务运维权限")
    raise NotImplementedYet("托管服务请在服务配置中修改副本数（或 HPA）后发布；纳管服务只更新镜像，不支持扩缩容")
