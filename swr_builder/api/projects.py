"""项目与命名空间分配。"""
from __future__ import annotations

from fastapi import APIRouter, Body, Request, Response

from ..authz import visible_project_ids
from ..errors import Conflict, NotFound
from ..services.access import project_or_404
from ..services.projects import check_project_payload, project_fields
from ..validation import require_empty
from ..web.deps import Ctx, User, audit

router = APIRouter()


@router.get("/api/projects")
async def list_projects(ctx: Ctx, user: User):
    ids = set(visible_project_ids(ctx.store, user))
    items = [p for p in ctx.store.list_projects() if p["id"] in ids]
    return {"items": items, "total": len(items)}


@router.post("/api/projects", status_code=201)
async def create_project(request: Request, ctx: Ctx, data: dict = Body(...)):
    check_project_payload(data)
    slug, name, registry_orgs = project_fields(data)
    project = ctx.store.create_project(slug, name, registry_orgs)
    audit(request, "project.create", project["id"], f"slug={slug}")
    return project


@router.get("/api/projects/{id}")
async def get_project(id: int, ctx: Ctx, user: User):
    return project_or_404(ctx.store, user, id)


@router.put("/api/projects/{id}")
async def update_project(id: int, request: Request, ctx: Ctx, user: User, data: dict = Body(...)):
    check_project_payload(data)
    current = project_or_404(ctx.store, user, id, "project.manage")
    slug, name, registry_orgs = project_fields(data, current["slug"])
    result = ctx.store.update_project(id, slug, name, registry_orgs)
    audit(request, "project.update", id)
    return result


@router.delete("/api/projects/{id}", status_code=204)
async def delete_project(id: int, request: Request, ctx: Ctx, user: User):
    project_or_404(ctx.store, user, id, "project.manage")
    if not ctx.store.delete_project(id):
        raise Conflict("项目仍有关联应用或任务，不能删除")
    audit(request, "project.delete", id)
    return Response(status_code=204)


@router.put("/api/projects/{id}/namespaces/{namespace_id}")
async def assign_namespace(id: int, namespace_id: int, request: Request, ctx: Ctx, user: User,
                           data: dict = Body(...)):
    require_empty(data)
    project_or_404(ctx.store, user, id, "project.manage")
    if not ctx.store.get_namespace(namespace_id):
        raise NotFound("命名空间不存在")
    ctx.store.assign_namespace(id, namespace_id)
    audit(request, "project.namespace.assign", f"{id}:{namespace_id}")
    return ctx.store.get_project(id)


@router.delete("/api/projects/{id}/namespaces/{namespace_id}", status_code=204)
async def unassign_namespace(id: int, namespace_id: int, request: Request, ctx: Ctx, user: User):
    project_or_404(ctx.store, user, id, "project.manage")
    if not ctx.store.unassign_namespace(id, namespace_id):
        raise Conflict("命名空间不存在或仍有关联服务")
    audit(request, "project.namespace.unassign", f"{id}:{namespace_id}")
    return Response(status_code=204)
