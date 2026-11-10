"""Role management, separate from account base roles."""
from fastapi import APIRouter, Body, Request, Response

from ..auth import PERMISSION_LABELS
from ..errors import Conflict, NotFound
from ..services.roles import role_catalog, normalize_role, SCOPE_PERMISSIONS
from ..web.deps import Ctx, audit

router = APIRouter()


@router.get("/api/role-definitions")
async def list_roles(ctx: Ctx):
    items = role_catalog(ctx.store)
    return {"items": items, "permissions": PERMISSION_LABELS, "scope_permissions": SCOPE_PERMISSIONS}


@router.post("/api/role-definitions", status_code=201)
async def create_role(request: Request, ctx: Ctx, data: dict = Body(...)):
    values = normalize_role(data)
    if any(r["code"] == values[0] for r in role_catalog(ctx.store)):
        raise Conflict("角色标识已存在")
    ctx.store.save_custom_role(*values)
    audit(request, "role.create", values[0])
    return next(r for r in role_catalog(ctx.store) if r["code"] == values[0])


@router.put("/api/role-definitions/{code}")
async def update_role(code: str, request: Request, ctx: Ctx, data: dict = Body(...)):
    current = next((r for r in ctx.store.list_custom_roles() if r["code"] == code), None)
    if not current:
        raise NotFound("自定义角色不存在；内置角色不可修改")
    ctx.store.save_custom_role(*normalize_role(data, current))
    audit(request, "role.update", code)
    return next(r for r in role_catalog(ctx.store) if r["code"] == code)


@router.delete("/api/role-definitions/{code}", status_code=204)
async def delete_role(code: str, request: Request, ctx: Ctx):
    if not any(r["code"] == code for r in ctx.store.list_custom_roles()):
        raise NotFound("自定义角色不存在；内置角色不可删除")
    if not ctx.store.delete_custom_role(code):
        raise Conflict("角色仍有有效授权，请先撤销成员授权或停用角色")
    audit(request, "role.delete", code)
    return Response(status_code=204)
