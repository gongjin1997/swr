"""资源管理 API 路由。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Request

from ..web.deps import Ctx, Me, audit, authenticated, requires
from ..services import resources

router = APIRouter()


@router.get("/api/resources")
@authenticated
async def list_resources(ctx: Ctx, me: Me, scope_type: Optional[str] = None, scope_id: Optional[int] = None,
                        type: Optional[str] = None):
    """列出资源"""
    items = resources.list_resources(ctx.store, me, scope_type, scope_id, type)
    return {"items": items, "total": len(items)}


@router.post("/api/resources", status_code=201)
@requires("resource.manage")
async def create_resource(request: Request, ctx: Ctx, me: Me, data: dict = Body(...)):
    """创建资源"""
    resource = resources.create_resource(ctx.store, ctx.box, me, data)
    audit(request, "resource.create", resource["id"],
          f"type={resource['type']};scope={resource['scope_type']}/{resource['scope_id']}")
    return resource


@router.get("/api/resources/{id}")
@authenticated
async def get_resource(id: int, ctx: Ctx, me: Me):
    """获取资源详情"""
    return resources.get_resource(ctx.store, me, id)


@router.put("/api/resources/{id}")
@authenticated
async def update_resource(id: int, request: Request, ctx: Ctx, me: Me, data: dict = Body(...)):
    """更新资源（创建新版本）"""
    resource = resources.update_resource(ctx.store, ctx.box, me, id, data)
    audit(request, "resource.update", id, f"version={resource['version']}")
    return resource


@router.delete("/api/resources/{id}", status_code=204)
@authenticated
async def delete_resource(id: int, request: Request, ctx: Ctx, me: Me):
    """删除资源"""
    resources.delete_resource(ctx.store, me, id)
    audit(request, "resource.delete", id, "")


@router.get("/api/resources/{id}/versions")
@authenticated
async def list_resource_versions(id: int, ctx: Ctx, me: Me):
    """列出资源版本历史"""
    versions = resources.list_resource_versions(ctx.store, me, id)
    return {"items": versions, "total": len(versions)}


@router.get("/api/resources/{id}/versions/{version}")
@authenticated
async def get_resource_version(id: int, version: int, ctx: Ctx, me: Me):
    """获取资源的指定版本"""
    return resources.get_resource_version(ctx.store, me, id, version)


@router.post("/api/resources/{id}/reveal")
@authenticated
async def reveal_resource(id: int, request: Request, ctx: Ctx, me: Me):
    """查看资源的敏感属性（解密）"""
    result = resources.reveal_resource(ctx.store, ctx.box, me, id)
    audit(request, "resource.reveal", id, "")
    return result


@router.get("/api/services/{id}/bindings")
@authenticated
async def list_service_bindings(id: int, ctx: Ctx, me: Me):
    """列出服务的资源绑定"""
    bindings = resources.list_service_bindings(ctx.store, me, id)
    return {"items": bindings, "total": len(bindings)}


@router.post("/api/services/{id}/bindings", status_code=201)
@authenticated
async def bind_resource(id: int, request: Request, ctx: Ctx, me: Me, data: dict = Body(...)):
    """绑定资源到服务"""
    binding = resources.bind_resource(ctx.store, me, id, data)
    audit(request, "service.bind_resource", id, f"resource={binding['resource_id']};alias={binding['alias']}")
    return binding


@router.delete("/api/services/{id}/bindings/{alias}", status_code=204)
@authenticated
async def unbind_resource(id: int, alias: str, request: Request, ctx: Ctx, me: Me):
    """解绑资源"""
    resources.unbind_resource(ctx.store, me, id, alias)
    audit(request, "service.unbind_resource", id, f"alias={alias}")


@router.get("/api/services/{id}/exports")
@authenticated
async def preview_exports(id: int, ctx: Ctx, me: Me, reveal: bool = False):
    """预览服务资源绑定的导出"""
    return resources.preview_exports(ctx.store, ctx.box, me, id, reveal)
