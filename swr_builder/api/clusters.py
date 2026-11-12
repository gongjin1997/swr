"""集群与命名空间纳管。业务规则见 services/clusters.py、services/namespaces.py。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Request

from ..services import clusters as cluster_service
from ..services import namespaces as namespace_service
from ..services.access import namespace_or_404
from ..services.discovery import discover_cluster, namespace_resources
from ..validation import require_empty
from ..web.deps import Ctx, Me, audit, requires

router = APIRouter()


# ------------------------------------------------------------------ 集群
@router.get("/api/clusters")
@requires("cluster.manage")
async def list_clusters(ctx: Ctx):
    items = cluster_service.list_clusters(ctx.store)
    return {"items": items, "total": len(items)}


@router.post("/api/clusters", status_code=201)
@requires("cluster.manage")
async def create_cluster(request: Request, ctx: Ctx, data: dict = Body(...)):
    cluster = cluster_service.create_cluster(ctx.store, ctx.box, data)
    audit(request, "cluster.create", cluster["id"], f"name={cluster['name']};auth_type={cluster['auth_type']}")
    return cluster


@router.get("/api/clusters/{id}")
@requires("cluster.manage")
async def get_cluster(id: int, ctx: Ctx):
    return cluster_service.public_cluster(ctx.store.get_cluster(id))


@router.put("/api/clusters/{id}")
@requires("cluster.manage")
async def update_cluster(id: int, request: Request, ctx: Ctx, data: dict = Body(...)):
    cluster = cluster_service.update_cluster(ctx.store, ctx.box, id, data)
    audit(request, "cluster.update", id, f"name={cluster['name']};credential={'credential' in data or 'config' in data}")
    return cluster


@router.post("/api/clusters/{id}/test")
@requires("cluster.manage")
async def test_cluster(id: int, request: Request, ctx: Ctx, data: dict = Body(default={})):
    require_empty(data)
    version = await cluster_service.verify_cluster(ctx.store, ctx.box, id, ctx.deploy_transport)
    audit(request, "cluster.test", id, f"version={version}")
    return {"ok": True, "version": version, "warnings": []}


@router.get("/api/clusters/{id}/discovery")
@requires("cluster.manage")
async def cluster_discovery(id: int, ctx: Ctx):
    return await discover_cluster(ctx, id)


@router.post("/api/clusters/{id}/rotate")
@requires("cluster.manage")
async def rotate_credential(id: int, request: Request, ctx: Ctx, data: dict = Body(...)):
    version = await cluster_service.rotate_credential(ctx.store, ctx.box, id, data, ctx.deploy_transport)
    audit(request, "cluster.credential.rotate", id, f"version={version}")
    return {"id": id, "status": "ready", "credential_expires_at": None}


@router.post("/api/clusters/{id}/revoke")
@requires("cluster.manage")
async def revoke_credential(id: int, ctx: Ctx, data: dict = Body(default={})):
    cluster_service.revoke_credential(ctx.store, id)


# ------------------------------------------------------------------ 命名空间
@router.get("/api/namespaces")
@requires("namespace.view")
async def list_namespaces(ctx: Ctx, me: Me, cluster_id: Optional[int] = None, tier: Optional[str] = None):
    if tier is not None:
        tier = namespace_service.normalize_tier(tier)
    items = [namespace_service.namespace_view(n)
             for n in ctx.store.list_namespaces(me.namespace_ids("namespace.view"), cluster_id, tier)]
    return {"items": items, "total": len(items)}


@router.post("/api/namespaces", status_code=201)
@requires("cluster.manage")
async def adopt_namespace(request: Request, ctx: Ctx, me: Me, data: dict = Body(...)):
    """纳管集群中已存在的 namespace（不会创建 namespace）。"""
    namespace, version = await namespace_service.adopt_namespace(ctx.store, ctx.box, me, data, ctx.deploy_transport)
    audit(request, "namespace.adopt", namespace["id"],
          f"cluster={namespace['cluster_id']};name={namespace['name']};tier={namespace['tier']};version={version}")
    return namespace


@router.get("/api/namespaces/{id}")
@requires("namespace.view")
async def get_namespace(id: int, ctx: Ctx, me: Me):
    return namespace_service.namespace_view(namespace_or_404(ctx.store, me, id))


@router.put("/api/namespaces/{id}")
@requires("cluster.manage")
async def update_namespace(id: int, request: Request, ctx: Ctx, data: dict = Body(...)):
    namespace = namespace_service.update_namespace(ctx.store, id, data)
    audit(request, "namespace.policy.update", id, f"tier={namespace['tier']}")
    return namespace


@router.get("/api/namespaces/{id}/resources")
@requires("namespace.view")
async def discover_namespace_resources(id: int, ctx: Ctx, me: Me):
    return await namespace_resources(ctx, namespace_or_404(ctx.store, me, id))


@router.post("/api/namespaces/{id}/review")
@requires("cluster.manage")
async def review_namespace(id: int, request: Request, ctx: Ctx, data: dict = Body(default={})):
    """用集群凭据做 SelfSubjectAccessReview，检查平台需要的 RBAC 权限是否齐全。"""
    require_empty(data)
    result = await namespace_service.review_namespace(ctx.store, ctx.box, id, ctx.deploy_transport)
    audit(request, "namespace.review", id, f"allowed={result['ok']}")
    return result


@router.get("/api/namespaces/{id}/rbac-yaml")
@requires("cluster.manage")
async def namespace_rbac_yaml(id: int, ctx: Ctx):
    return namespace_service.rbac_template(ctx.store, id)
