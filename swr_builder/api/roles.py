"""角色定义：内置角色只读，自定义角色可新建、修改、启停与删除。"""
from fastapi import APIRouter, Body, Request, Response

from ..services import roles as role_service
from ..web.deps import Ctx, audit, authenticated, requires

router = APIRouter()


@router.get("/api/role-definitions")
@authenticated
async def list_roles(ctx: Ctx):
    return role_service.catalog_payload(ctx.store)


@router.post("/api/role-definitions", status_code=201)
@requires("role.manage")
async def create_role(request: Request, ctx: Ctx, data: dict = Body(...)):
    role = role_service.create_role(ctx.store, data)
    audit(request, "role.create", role["code"], f"scope={role['scope_type']};permissions={','.join(role['permissions'])}")
    return role


@router.put("/api/role-definitions/{code}")
@requires("role.manage")
async def update_role(code: str, request: Request, ctx: Ctx, data: dict = Body(...)):
    role = role_service.update_role(ctx.store, code, data)
    audit(request, "role.update", code,
          f"enabled={role['enabled']};permissions={','.join(role['permissions'])}")
    return role


@router.delete("/api/role-definitions/{code}", status_code=204)
@requires("role.manage")
async def delete_role(code: str, request: Request, ctx: Ctx):
    role_service.delete_role(ctx.store, code)
    audit(request, "role.delete", code)
    return Response(status_code=204)
