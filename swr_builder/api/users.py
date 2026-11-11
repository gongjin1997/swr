"""账号管理。规则见 services/accounts.py。"""
from __future__ import annotations

from fastapi import APIRouter, Body, Request

from ..access import BUILTIN_ROLES, PERMISSIONS
from ..services import accounts
from ..web.deps import Ctx, Me, audit, requires

router = APIRouter()


@router.get("/api/roles")
@requires("user.manage")
async def legacy_roles():
    """v1.3 的基础角色说明，仅为旧客户端保留；新页面使用 /api/role-definitions。"""
    mapping = {"admin": "platform_admin", "developer": "developer", "viewer": "project_viewer"}
    labels = {"admin": "管理员", "developer": "开发者", "viewer": "只读用户"}
    return {"roles": [{"value": value, "label": labels[value],
                       "permissions": sorted(BUILTIN_ROLES[role].effective)} for value, role in mapping.items()],
            "permissions": {code: p.label for code, p in PERMISSIONS.items()}, "deprecated": True}


@router.get("/api/users")
@requires("user.manage")
async def list_users(ctx: Ctx, me: Me):
    return accounts.list_accounts(ctx.store, me)


@router.get("/api/users/{id}")
@requires("user.manage")
async def get_user(id: int, ctx: Ctx, me: Me):
    return accounts.account_detail(ctx.store, me, id)


@router.post("/api/users", status_code=201)
@requires("user.manage")
async def create_user(request: Request, ctx: Ctx, me: Me, data: dict = Body(...)):
    account = accounts.create_account(ctx.store, ctx.auth, me, data)
    audit(request, "user.create", account["username"],
          f"platform_admin={account['is_platform_admin']};grants={account['grant_count']}")
    return account


@router.put("/api/users/{id}")
@requires("user.manage")
async def update_user(id: int, request: Request, ctx: Ctx, me: Me, data: dict = Body(...)):
    account, changes = accounts.update_account(ctx.store, me, id, data)
    audit(request, "user.update", account["username"], ",".join(changes))
    return account


@router.delete("/api/users/{id}")
@requires("user.manage")
async def delete_user(id: int, request: Request, ctx: Ctx, me: Me):
    user = accounts.delete_account(ctx.store, me, id)
    audit(request, "user.delete", user["username"])
    return {"ok": True}


@router.post("/api/users/{id}/reset-password")
@requires("user.manage")
async def reset_password(id: int, request: Request, ctx: Ctx, me: Me, data: dict = Body(default={})):
    user, password = accounts.reset_password(ctx.store, ctx.auth, me, id, data)
    audit(request, "user.reset_password", user["username"])
    return {"password": password, "must_change_password": True}
