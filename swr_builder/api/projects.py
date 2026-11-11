"""项目与命名空间分配。"""
from __future__ import annotations

from fastapi import APIRouter, Body, Request, Response

from ..errors import Conflict, NotFound
from ..services.access import project_or_404
from ..services.projects import check_project_payload, project_fields
from ..validation import require_empty
from ..web.deps import Ctx, Me, audit, requires

router = APIRouter()


@router.get("/api/projects")
@requires("project.view")
async def list_projects(ctx: Ctx, me: Me):
    ids = me.project_ids("project.view")
    items = [p for p in ctx.store.list_projects() if ids is None or p["id"] in ids]
    return {"items": items, "total": len(items)}


@router.post("/api/projects", status_code=201)
@requires("project.manage")
async def create_project(request: Request, ctx: Ctx, data: dict = Body(...)):
    check_project_payload(data)
    slug, name, registry_orgs = project_fields(data)
    project = ctx.store.create_project(slug, name, registry_orgs)
    audit(request, "project.create", project["id"], f"slug={slug}")
    return project


@router.get("/api/projects/{id}")
@requires("project.view")
async def get_project(id: int, ctx: Ctx, me: Me):
    return project_or_404(ctx.store, me, id)


@router.put("/api/projects/{id}")
@requires("project.manage")
async def update_project(id: int, request: Request, ctx: Ctx, me: Me, data: dict = Body(...)):
    check_project_payload(data)
    current = project_or_404(ctx.store, me, id, "project.manage")
    slug, name, registry_orgs = project_fields(data, current["slug"])
    result = ctx.store.update_project(id, slug, name, registry_orgs)
    audit(request, "project.update", id)
    return result


@router.delete("/api/projects/{id}", status_code=204)
@requires("project.manage")
async def delete_project(id: int, request: Request, ctx: Ctx, me: Me):
    project_or_404(ctx.store, me, id, "project.manage")
    if not ctx.store.delete_project(id):
        raise Conflict("项目仍有关联应用、任务或成员授权，不能删除")
    audit(request, "project.delete", id)
    return Response(status_code=204)


@router.put("/api/projects/{id}/namespaces/{namespace_id}")
@requires("project.manage")
async def assign_namespace(id: int, namespace_id: int, request: Request, ctx: Ctx, me: Me, data: dict = Body(...)):
    require_empty(data)
    project_or_404(ctx.store, me, id, "project.manage")
    if not ctx.store.get_namespace(namespace_id):
        raise NotFound("命名空间不存在")
    ctx.store.assign_namespace(id, namespace_id)
    audit(request, "project.namespace.assign", f"{id}:{namespace_id}")
    return ctx.store.get_project(id)


@router.delete("/api/projects/{id}/namespaces/{namespace_id}", status_code=204)
@requires("project.manage")
async def unassign_namespace(id: int, namespace_id: int, request: Request, ctx: Ctx, me: Me):
    project_or_404(ctx.store, me, id, "project.manage")
    if not ctx.store.unassign_namespace(id, namespace_id):
        raise Conflict("命名空间不存在或仍有关联服务")
    audit(request, "project.namespace.unassign", f"{id}:{namespace_id}")
    return Response(status_code=204)
