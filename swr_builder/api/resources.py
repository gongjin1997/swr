"""资源管理 API 路由。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Query, Request, Response

from ..services import resources
from ..web.deps import Ctx, Me, audit, authenticated

router = APIRouter()


@router.get("/api/resources")
@authenticated
async def list_resources(ctx: Ctx, me: Me, scope_type: Optional[str] = Query(None),
                        scope_id: Optional[int] = Query(None), type: Optional[str] = Query(None),
                        limit: int = Query(100), offset: int = Query(0)):
    items, total = resources.list_resources(ctx.store, ctx.box, me, scope_type, scope_id, type, limit, offset)
    return {"items": items, "total": total}


@router.post("/api/resources", status_code=201)
@authenticated
async def create_resource(request: Request, ctx: Ctx, me: Me, data: dict = Body(...)):
    resource = resources.create_resource(ctx.store, ctx.box, me, data)
    audit(request, "resource.create", resource["id"],
          f"type={resource['type']};scope={resource['scope_type']}/{resource['scope_id']}")
    return resource


@router.get("/api/resources/{id}")
@authenticated
async def get_resource(id: int, ctx: Ctx, me: Me):
    return resources.get_resource(ctx.store, ctx.box, me, id)


@router.put("/api/resources/{id}")
@authenticated
async def update_resource(id: int, request: Request, ctx: Ctx, me: Me, data: dict = Body(...)):
    resource = resources.update_resource(ctx.store, ctx.box, me, id, data)
    audit(request, "resource.update", id, f"version={resource['version']}")
    return resource


@router.delete("/api/resources/{id}", status_code=204)
@authenticated
async def delete_resource(id: int, request: Request, ctx: Ctx, me: Me):
    resources.delete_resource(ctx.store, me, id)
    audit(request, "resource.delete", id, "")
    return Response(status_code=204)


@router.get("/api/resources/{id}/versions")
@authenticated
async def list_resource_versions(id: int, ctx: Ctx, me: Me):
    versions = resources.list_resource_versions(ctx.store, me, id)
    return {"items": versions, "total": len(versions)}


@router.get("/api/resources/{id}/versions/{version}")
@authenticated
async def get_resource_version(id: int, version: int, ctx: Ctx, me: Me):
    return resources.get_resource_version(ctx.store, ctx.box, me, id, version)


@router.post("/api/resources/{id}/reveal")
@authenticated
async def reveal_resource(id: int, request: Request, ctx: Ctx, me: Me):
    result = resources.reveal_resource(ctx.store, ctx.box, me, id)
    audit(request, "resource.reveal", id, f"version={result['version']}")
    return result


@router.get("/api/services/{id}/bindings")
@authenticated
async def list_service_bindings(id: int, ctx: Ctx, me: Me):
    bindings = resources.list_service_bindings(ctx.store, ctx.box, me, id)
    return {"items": bindings, "total": len(bindings)}


@router.post("/api/services/{id}/bindings", status_code=201)
@authenticated
async def bind_resource(id: int, request: Request, ctx: Ctx, me: Me, data: dict = Body(...)):
    binding = resources.bind_resource(ctx.store, ctx.box, me, id, data)
    audit(request, "service.bind_resource", id, f"resource={binding['resource_id']};alias={binding['alias']}")
    return binding


@router.delete("/api/services/{id}/bindings/{resource_id}", status_code=204)
@authenticated
async def unbind_resource(id: int, resource_id: int, request: Request, ctx: Ctx, me: Me):
    resources.unbind_resource(ctx.store, me, id, resource_id)
    audit(request, "service.unbind_resource", id, f"resource={resource_id}")
    return Response(status_code=204)
