"""项目与命名空间分配。业务规则见 services/projects.py。"""
from __future__ import annotations

from fastapi import APIRouter, Body, Request, Response

from ..services import projects as project_service
from ..services.access import project_or_404
from ..web.deps import Ctx, Me, audit, requires

router = APIRouter()


@router.get("/api/projects")
@requires("project.view")
async def list_projects(ctx: Ctx, me: Me):
    items = project_service.list_projects(ctx.store, me)
    return {"items": items, "total": len(items)}


@router.post("/api/projects", status_code=201)
@requires("project.manage")
async def create_project(request: Request, ctx: Ctx, data: dict = Body(...)):
    project = project_service.create_project(ctx.store, data)
    audit(request, "project.create", project["id"], f"slug={project['slug']}")
    return project


@router.get("/api/projects/{id}")
@requires("project.view")
async def get_project(id: int, ctx: Ctx, me: Me):
    return project_or_404(ctx.store, me, id)


@router.put("/api/projects/{id}")
@requires("project.manage")
async def update_project(id: int, request: Request, ctx: Ctx, me: Me, data: dict = Body(...)):
    project = project_service.update_project(ctx.store, me, id, data)
    audit(request, "project.update", id, f"slug={project['slug']}")
    return project


@router.delete("/api/projects/{id}", status_code=204)
@requires("project.manage")
async def delete_project(id: int, request: Request, ctx: Ctx, me: Me):
    project_service.delete_project(ctx.store, me, id)
    audit(request, "project.delete", id)
    return Response(status_code=204)


@router.put("/api/projects/{id}/namespaces/{namespace_id}")
@requires("project.manage")
async def assign_namespace(id: int, namespace_id: int, request: Request, ctx: Ctx, me: Me, data: dict = Body(...)):
    project = project_service.assign_namespace(ctx.store, me, id, namespace_id, data)
    audit(request, "project.namespace.assign", f"{id}:{namespace_id}")
    return project


@router.delete("/api/projects/{id}/namespaces/{namespace_id}", status_code=204)
@requires("project.manage")
async def unassign_namespace(id: int, namespace_id: int, request: Request, ctx: Ctx, me: Me):
    project_service.unassign_namespace(ctx.store, me, id, namespace_id)
    audit(request, "project.namespace.unassign", f"{id}:{namespace_id}")
    return Response(status_code=204)
