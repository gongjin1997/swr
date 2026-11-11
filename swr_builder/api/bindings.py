"""成员授权。规则见 services/grants.py。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Request, Response

from ..access import SCOPE_TYPES, role_index
from ..services import grants
from ..services.accounts import require_member_admin
from ..web.deps import Ctx, Me, audit, authenticated

router = APIRouter()


@router.get("/api/bindings")
@authenticated
async def list_bindings(ctx: Ctx, me: Me, user_id: Optional[int] = None, scope_type: Optional[str] = None,
                        scope_id: Optional[int] = None, role: Optional[str] = None):
    """平台管理员与账号管理员看到全部授权；项目/命名空间成员管理员只看到自己管理范围内的授权。"""
    require_member_admin(me)
    if scope_type is not None and scope_type not in SCOPE_TYPES:
        raise ValueError("授权范围无效")
    items = grants.visible_binding_rows(ctx.store, me, user_id=user_id, scope_type=scope_type, scope_id=scope_id,
                                        role=role)
    return {"items": items, "total": len(items)}


@router.get("/api/bindings/options")
@authenticated
async def binding_options(ctx: Ctx, me: Me):
    """当前账号可以授予的范围、资源与角色。"""
    require_member_admin(me)
    return grants.grant_options(ctx.store, me)


@router.get("/api/bindable-users")
@authenticated
async def bindable_users(ctx: Ctx, me: Me):
    require_member_admin(me)
    items = [{"id": row["id"], "username": row["username"], "display_name": row.get("display_name") or row["username"],
              "enabled": bool(row["enabled"])} for row in ctx.store.list_users() if row["enabled"]]
    return {"items": items, "total": len(items)}


@router.post("/api/bindings", status_code=201)
@authenticated
async def create_binding(request: Request, ctx: Ctx, me: Me, data: dict = Body(...)):
    binding = grants.grant(ctx.store, me, data)
    audit(request, "binding.create", binding["id"], grants.describe(binding, role_index(ctx.store)))
    return grants.binding_view(binding)


@router.delete("/api/bindings/{id}", status_code=204)
@authenticated
async def delete_binding(id: int, request: Request, ctx: Ctx, me: Me):
    binding = grants.revoke(ctx.store, me, id)
    audit(request, "binding.delete", id, grants.describe(binding, role_index(ctx.store)))
    return Response(status_code=204)
