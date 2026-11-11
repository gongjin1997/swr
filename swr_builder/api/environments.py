"""环境、负载均衡入口、分层配置集与证书。业务规则见 services/environments.py、configsets.py、certificates.py。"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Body, Query, Request, Response

from ..errors import Forbidden
from ..services import certificates as certificate_service
from ..services import configsets as config_service
from ..services import environments as environment_service
from ..services.access import namespace_or_404, project_or_404
from ..web.deps import Ctx, Me, audit, authenticated, requires

router = APIRouter()


# ------------------------------------------------------------------ 环境
@router.get("/api/environments")
@requires("namespace.view")
async def list_environments(ctx: Ctx, me: Me):
    items = environment_service.list_environments(ctx.store, me)
    return {"items": items, "total": len(items)}


@router.get("/api/environments/compare")
@requires("namespace.view")
async def compare_environments(ctx: Ctx, me: Me, left: int = Query(...), right: int = Query(...),
                               project_id: Optional[int] = Query(None)):
    if project_id is not None:
        project_or_404(ctx.store, me, project_id)
    return config_service.compare_environments(ctx.store, ctx.box, me, left, right, project_id)


@router.get("/api/environments/{id}")
@requires("namespace.view")
async def get_environment(id: int, ctx: Ctx, me: Me):
    return environment_service.get_environment(ctx.store, me, id)


@router.put("/api/environments/{id}/profile")
@requires("environment.config")
async def save_environment_profile(id: int, request: Request, ctx: Ctx, me: Me, data: dict = Body(...)):
    environment = environment_service.save_profile(ctx.store, me, id, data)
    audit(request, "environment.profile.update", id, f"version={environment['profile_version']};"
          f"ingress_profile={environment['ingress_profile_id']}")
    return environment


@router.get("/api/environments/{id}/effective-config")
@requires("namespace.view")
async def effective_config(id: int, ctx: Ctx, me: Me, project_id: Optional[int] = Query(None)):
    namespace_or_404(ctx.store, me, id)
    if project_id is not None:
        project_or_404(ctx.store, me, project_id)
    return config_service.effective_config(ctx.store, me, id, project_id)


# ------------------------------------------------------------------ 负载均衡入口
@router.get("/api/ingress-profiles")
@authenticated
async def list_ingress_profiles(ctx: Ctx, me: Me):
    items = environment_service.list_ingress_profiles(ctx.store, me)
    return {"items": items, "total": len(items)}


@router.post("/api/ingress-profiles", status_code=201)
@requires("certificate.manage")
async def create_ingress_profile(request: Request, ctx: Ctx, me: Me, data: dict = Body(...)):
    profile = environment_service.create_ingress_profile(ctx.store, me.id, data)
    audit(request, "ingress_profile.create", profile["id"], f"name={profile['name']}")
    return profile


@router.put("/api/ingress-profiles/{id}")
@requires("certificate.manage")
async def update_ingress_profile(id: int, request: Request, ctx: Ctx, me: Me, data: dict = Body(...)):
    profile = environment_service.update_ingress_profile(ctx.store, me.id, id, data)
    audit(request, "ingress_profile.update", id, f"version={profile['version']}")
    return profile


@router.delete("/api/ingress-profiles/{id}", status_code=204)
@requires("certificate.manage")
async def delete_ingress_profile(id: int, request: Request, ctx: Ctx):
    environment_service.delete_ingress_profile(ctx.store, id)
    audit(request, "ingress_profile.delete", id)
    return Response(status_code=204)


# ------------------------------------------------------------------ 配置集
@router.get("/api/config-sets")
@authenticated
async def list_config_sets(ctx: Ctx, me: Me, scope_type: str = Query(...), scope_id: int = Query(0)):
    items = config_service.list_config_sets(ctx.store, me, scope_type, scope_id)
    return {"items": items, "total": len(items)}


@router.post("/api/config-sets", status_code=201)
@authenticated
async def create_config_set(request: Request, ctx: Ctx, me: Me, data: dict = Body(...)):
    item = config_service.create_config_set(ctx.store, ctx.box, me, data)
    audit(request, "config.create", item["id"], f"scope={item['scope_type']}:{item['scope_id']};name={item['name']};"
          f"kind={item['kind']};keys={len(item['entries'])}")
    return item


@router.get("/api/config-sets/{id}")
@authenticated
async def get_config_set(id: int, ctx: Ctx, me: Me):
    return config_service.public_config_set(ctx.store, me, config_service.config_set_or_404(ctx.store, me, id))


@router.put("/api/config-sets/{id}")
@authenticated
async def update_config_set(id: int, request: Request, ctx: Ctx, me: Me, data: dict = Body(...)):
    item = config_service.update_config_set(ctx.store, ctx.box, me, id, data)
    audit(request, "config.update", id, f"name={item['name']};version={item['version']}")
    return item


@router.delete("/api/config-sets/{id}", status_code=204)
@authenticated
async def delete_config_set(id: int, request: Request, ctx: Ctx, me: Me):
    item = config_service.delete_config_set(ctx.store, me, id)
    audit(request, "config.delete", id, f"scope={item['scope_type']}:{item['scope_id']};name={item['name']}")
    return Response(status_code=204)


@router.get("/api/config-sets/{id}/versions")
@authenticated
async def config_versions(id: int, ctx: Ctx, me: Me):
    items = config_service.versions(ctx.store, me, id)
    return {"items": items, "total": len(items)}


@router.get("/api/config-sets/{id}/versions/{number}")
@authenticated
async def config_version(id: int, number: int, ctx: Ctx, me: Me):
    return config_service.version_detail(ctx.store, me, id, number)


@router.get("/api/config-sets/{id}/diff")
@authenticated
async def config_diff(id: int, ctx: Ctx, me: Me, old: int = Query(..., alias="from"), new: int = Query(..., alias="to")):
    changes = config_service.diff(ctx.store, ctx.box, me, id, old, new)
    return {"items": changes, "total": len(changes)}


@router.post("/api/config-sets/{id}/reveal")
@authenticated
async def reveal_config_secrets(id: int, request: Request, ctx: Ctx, me: Me, data: dict = Body(default={})):
    keys = data.get("keys")
    if keys is not None and (not isinstance(keys, list) or not all(isinstance(k, str) for k in keys)):
        raise ValueError("keys 必须是字符串数组")
    number = data.get("version")
    if number is not None and (not isinstance(number, int) or isinstance(number, bool)):
        raise ValueError("version 必须是整数")
    values = config_service.reveal(ctx.store, ctx.box, me, id, keys, number)
    audit(request, "config.secret.view", id, f"version={number or 'current'};keys={','.join(values)[:800]}")
    return {"values": values}


# ------------------------------------------------------------------ 证书
@router.get("/api/certificates")
@requires("certificate.view")
async def list_certificates(ctx: Ctx):
    items = certificate_service.list_certificates(ctx.store)
    return {"items": items, "total": len(items)}


@router.get("/api/certificates/options")
@authenticated
async def certificate_options(ctx: Ctx, me: Me):
    """配置页选择证书：环境配置或平台、项目层配置的维护者可用。"""
    if not (me.can("certificate.view") or me.can_anywhere("environment.config") or me.can_anywhere("config.manage")):
        raise Forbidden("没有选择证书的权限")
    items = certificate_service.certificate_options(ctx.store)
    return {"items": items, "total": len(items)}


@router.post("/api/certificates", status_code=201)
@requires("certificate.manage")
async def create_certificate(request: Request, ctx: Ctx, me: Me, data: dict = Body(...)):
    item = certificate_service.create_certificate(ctx.store, ctx.box, me.id, data)
    audit(request, "certificate.create", item["id"], f"name={item['name']};kind={item['kind']};sha256={item['sha256']}")
    return item


@router.get("/api/certificates/{id}")
@requires("certificate.view")
async def get_certificate(id: int, ctx: Ctx):
    return certificate_service.certificate_detail(ctx.store, id)


@router.post("/api/certificates/{id}/versions", status_code=201)
@requires("certificate.manage")
async def rotate_certificate(id: int, request: Request, ctx: Ctx, me: Me, data: dict = Body(...)):
    item = certificate_service.rotate_certificate(ctx.store, ctx.box, me.id, id, data)
    audit(request, "certificate.rotate", id, f"version={item['version']};sha256={item['sha256']}")
    return item


@router.get("/api/certificates/{id}/versions/{number}/content")
@requires("secret.view")
async def certificate_content(id: int, number: int, request: Request, ctx: Ctx, me: Me):
    if not me.can("secret.view"):
        raise Forbidden("下载证书文件需要全局的敏感配置查看权限")
    content = certificate_service.certificate_content(ctx.store, ctx.box, id, number)
    audit(request, "certificate.download", id, f"version={number};filename={content['filename']}")
    return content


@router.delete("/api/certificates/{id}", status_code=204)
@requires("certificate.manage")
async def delete_certificate(id: int, request: Request, ctx: Ctx):
    certificate_service.certificate_or_404(ctx.store, id)
    ctx.store.delete_certificate(id)
    audit(request, "certificate.delete", id)
    return Response(status_code=204)


__all__: List[str] = ["router"]
