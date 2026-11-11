"""集群与命名空间纳管。"""
from __future__ import annotations

import sqlite3
from typing import Optional

from fastapi import APIRouter, Body, Request

from ..deploy.k8s_client import KubeClient
from ..errors import Conflict, DomainError, NotFound, NotImplementedYet, Unavailable
from ..services import clusters as cluster_service
from ..services.access import namespace_or_404
from ..services.discovery import discover_cluster, namespace_resources
from ..services.namespaces import TIERS, normalize_namespace_policy, rbac_checks, rbac_yaml
from ..validation import DNS_LABEL_RE, reject_unknown, require_empty, text_field
from ..web.deps import Ctx, Me, audit, requires

router = APIRouter()


# ------------------------------------------------------------------ 集群
@router.get("/api/clusters")
@requires("cluster.manage")
async def list_clusters(ctx: Ctx):
    items = [cluster_service.public_cluster(row) for row in ctx.store.list_clusters()]
    return {"items": items, "total": len(items)}


@router.post("/api/clusters", status_code=201)
@requires("cluster.manage")
async def create_cluster(request: Request, ctx: Ctx, data: dict = Body(...)):
    name, server, auth_type, duration, credential = cluster_service.normalize_cluster_input(data)
    cluster_id = ctx.store.create_cluster({
        "name": name, "server": server, "auth_type": auth_type,
        "credential_encrypted": cluster_service.encrypt_credential(ctx.box, credential),
        "cce_cluster_id": text_field(data, "cce_cluster_id", 128) or None, "cert_duration_days": duration})
    audit(request, "cluster.create", cluster_id, f"auth_type={auth_type}")
    return cluster_service.public_cluster(ctx.store.get_cluster(cluster_id))


@router.get("/api/clusters/{id}")
@requires("cluster.manage")
async def get_cluster(id: int, ctx: Ctx):
    return cluster_service.public_cluster(ctx.store.get_cluster(id))


@router.put("/api/clusters/{id}")
@requires("cluster.manage")
async def update_cluster(id: int, request: Request, ctx: Ctx, data: dict = Body(...)):
    reject_unknown(data, cluster_service.CLUSTER_FIELDS, "集群请求包含未知字段")
    existing = ctx.store.get_cluster_record(id)
    if not existing:
        raise NotFound("集群不存在")
    name, server, auth_type, duration, credential = cluster_service.normalize_cluster_input(data, existing)
    encrypted = existing["credential_encrypted"]
    if credential is not None:
        encrypted = cluster_service.encrypt_credential(ctx.box, credential)
    ctx.store.update_cluster(id, {
        "name": name, "server": server, "auth_type": auth_type, "credential_encrypted": encrypted,
        "cce_cluster_id": text_field(data, "cce_cluster_id", 128) or existing["cce_cluster_id"],
        "credential_expires_at": existing["credential_expires_at"], "cert_duration_days": duration,
        "status": "unverified"})
    audit(request, "cluster.update", id)
    return cluster_service.public_cluster(ctx.store.get_cluster(id))


@router.post("/api/clusters/{id}/test")
@requires("cluster.manage")
async def test_cluster(id: int, request: Request, ctx: Ctx, data: dict = Body(...)):
    require_empty(data)
    cluster, credential = cluster_service.load_cluster(ctx.store, ctx.box, id)
    version = await cluster_service.test_connection(cluster, credential, ctx.deploy_transport)
    ctx.store.set_cluster_status(id, "ready")
    audit(request, "cluster.test", id, f"version={version}")
    return {"ok": True, "version": version, "warnings": []}


@router.get("/api/clusters/{id}/discovery")
@requires("cluster.manage")
async def cluster_discovery(id: int, ctx: Ctx):
    return await discover_cluster(ctx, id)


@router.post("/api/clusters/{id}/rotate")
@requires("cluster.manage")
async def rotate_credential(id: int, request: Request, ctx: Ctx, data: dict = Body(...)):
    if set(data) - {"credential"} or "credential" not in data:
        raise ValueError("请提供新凭据")
    cluster = ctx.store.get_cluster_record(id)
    if not cluster:
        raise NotFound("集群不存在")
    credential = cluster_service.normalize_credential(data["credential"], cluster["auth_type"])
    candidate = cluster_service.encrypt_credential(ctx.box, credential)
    version = await cluster_service.test_connection(cluster, credential, ctx.deploy_transport)
    ctx.store.update_cluster(id, {**cluster, "credential_encrypted": candidate, "status": "ready"})
    audit(request, "cluster.credential.rotate", id, f"version={version}")
    return {"id": id, "status": "ready", "credential_expires_at": None}


@router.post("/api/clusters/{id}/revoke")
@requires("cluster.manage")
async def revoke_credential(id: int, ctx: Ctx, data: dict = Body(...)):
    cluster = ctx.store.get_cluster(id)
    if not cluster:
        raise NotFound("集群不存在")
    if cluster["auth_type"] != "cce_cert":
        raise DomainError("只有 CCE 签发证书支持撤销")
    raise NotImplementedYet("CCE 证书撤销尚未接入现场 CCE API")


# ------------------------------------------------------------------ 命名空间
@router.get("/api/namespaces")
@requires("namespace.view")
async def list_namespaces(ctx: Ctx, me: Me, cluster_id: Optional[int] = None, tier: Optional[str] = None):
    if tier is not None and tier not in TIERS:
        raise ValueError("tier 无效")
    items = ctx.store.list_namespaces(me.namespace_ids("namespace.view"), cluster_id, tier)
    return {"items": items, "total": len(items)}


@router.post("/api/namespaces", status_code=201)
@requires("cluster.manage")
async def adopt_namespace(request: Request, ctx: Ctx, me: Me, data: dict = Body(...)):
    """纳管集群中已存在的 namespace（不会创建 namespace）。"""
    reject_unknown(data, {"name", "cluster_id", "tier", "policy", "project_id"}, "命名空间请求包含未知字段")
    name, cluster_id, tier = text_field(data, "name", 63).lower(), data.get("cluster_id"), data.get("tier")
    tier = "staging" if tier == "uat" else tier
    if not DNS_LABEL_RE.fullmatch(name):
        raise ValueError("namespace 必须是 DNS-1123 label")
    if type(cluster_id) is not int or not ctx.store.get_cluster(cluster_id):
        raise ValueError("集群不存在")
    if tier not in TIERS:
        raise ValueError("tier 必须为 dev/test/staging/prod")
    policy = normalize_namespace_policy(data.get("policy"))
    project_id = data.get("project_id")
    if project_id is not None and (type(project_id) is not int or not ctx.store.get_project(project_id)
                                   or not me.can_project(project_id, "project.manage")):
        raise NotFound("可分配的项目不存在")
    cluster, credential = cluster_service.load_cluster(ctx.store, ctx.box, cluster_id)
    version = await cluster_service.test_connection(cluster, credential, ctx.deploy_transport)
    async with KubeClient(cluster["server"], credential, ctx.deploy_transport) as kube:
        status = await kube.namespace_status(name)
    if status == 404:
        raise DomainError("目标 namespace 在集群中不存在；本接口只纳管现有 namespace")
    if status != 200:
        raise Unavailable(f"读取目标 namespace 失败，HTTP {status}")
    try:
        namespace_id = ctx.store.create_namespace(cluster_id, name, tier, policy, project_id)
    except sqlite3.IntegrityError as exc:
        if "UNIQUE constraint failed" in str(exc):
            raise Conflict("此命名空间已纳管，请刷新列表") from None
        raise
    ctx.store.set_cluster_status(cluster_id, "ready")
    audit(request, "namespace.adopt", namespace_id, f"cluster={cluster_id} version={version}")
    return ctx.store.get_namespace(namespace_id)


@router.get("/api/namespaces/{id}")
@requires("namespace.view")
async def get_namespace(id: int, ctx: Ctx, me: Me):
    return namespace_or_404(ctx.store, me, id)


@router.put("/api/namespaces/{id}")
@requires("cluster.manage")
async def update_namespace(id: int, request: Request, ctx: Ctx, data: dict = Body(...)):
    reject_unknown(data, {"tier", "policy"}, "命名空间请求包含未知字段")
    current = ctx.store.get_namespace(id)
    if not current:
        raise NotFound("命名空间不存在")
    tier = data.get("tier", current["tier"])
    tier = "staging" if tier == "uat" else tier
    if tier not in TIERS:
        raise ValueError("tier 无效")
    policy = normalize_namespace_policy(data.get("policy", current["policy"]))
    ctx.store.update_namespace(id, tier, policy)
    audit(request, "namespace.policy.update", id)
    return ctx.store.get_namespace(id)


@router.get("/api/namespaces/{id}/resources")
@requires("namespace.view")
async def discover_namespace_resources(id: int, ctx: Ctx, me: Me):
    return await namespace_resources(ctx, namespace_or_404(ctx.store, me, id))


@router.post("/api/namespaces/{id}/review")
@requires("cluster.manage")
async def review_namespace(id: int, request: Request, ctx: Ctx, data: dict = Body(...)):
    """用集群凭据做 SelfSubjectAccessReview，检查平台需要的 RBAC 权限是否齐全。"""
    require_empty(data)
    namespace = ctx.store.get_namespace(id)
    if not namespace:
        raise NotFound("命名空间不存在")
    cluster, credential = cluster_service.load_cluster(ctx.store, ctx.box, namespace["cluster_id"])
    checks, warnings = [], []
    async with KubeClient(cluster["server"], credential, ctx.deploy_transport) as kube:
        for group, resource, verb in rbac_checks(namespace["policy"]):
            status = await kube.access_review(namespace["name"], group, resource, verb)
            checks.append({"group": group, "resource": resource, "verb": verb, "allowed": status.get("allowed") is True})
            if status.get("evaluationError"):
                warnings.append(f"{resource}/{verb}: {status['evaluationError'][:200]}")
    allowed = all(c["allowed"] for c in checks)
    audit(request, "namespace.review", id, f"allowed={allowed}")
    return {"ok": allowed and not warnings, "checks": checks, "warnings": warnings}


@router.get("/api/namespaces/{id}/rbac-yaml")
@requires("cluster.manage")
async def namespace_rbac_yaml(id: int, ctx: Ctx):
    namespace = ctx.store.get_namespace(id)
    if not namespace:
        raise NotFound("命名空间不存在")
    return {"yaml": rbac_yaml(namespace["name"]),
            "warning": "权限模板供集群管理员审核；写 Secret 权限与 Pod 创建存在间接读取 Secret 的风险。"}
