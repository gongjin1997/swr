"""账号管理（平台管理员）。"""
from __future__ import annotations

from fastapi import APIRouter, Body, Request

from ..auth import PERMISSION_LABELS, ROLES, USERNAME_RE, permissions_of, public_user
from ..authz import has_global_permission
from ..errors import Forbidden
from ..security import hash_password
from ..services.access import user_or_404
from ..services.users import administrator_account, protect_user
from ..validation import text_field
from ..web.deps import Ctx, User, audit

router = APIRouter()


@router.get("/api/roles")
async def roles():
    return {"roles": [{"value": r, "label": label, "permissions": permissions_of(r)} for r, label in ROLES.items()],
            "permissions": PERMISSION_LABELS}


@router.get("/api/users")
async def list_users(ctx: Ctx):
    return [public_user(u) for u in ctx.store.list_users()]


@router.post("/api/users", status_code=201)
async def create_user(request: Request, ctx: Ctx, actor: User, data: dict = Body(...)):
    name = text_field(data, "username", 32)
    if not USERNAME_RE.fullmatch(name):
        raise ValueError("用户名为 2–32 位字母、数字、_ . @ -，以字母或数字开头")
    if ctx.store.get_user_by_name(name):
        raise ValueError("用户名已存在")
    role = data.get("role", "developer")
    if not isinstance(role, str) or role not in ROLES:
        raise ValueError("角色无效")
    if role == "admin" and not has_global_permission(ctx.store, actor, "platform.admin"):
        raise Forbidden("只有平台管理员可以创建管理员账号")
    password = data.get("password", "")
    if not isinstance(password, str):
        raise ValueError("密码格式无效")
    ctx.auth.validate_password(password)
    created = ctx.store.create_user(username=name, display_name=text_field(data, "display_name") or name,
                                    email=text_field(data, "email", 254), role=role,
                                    password_hash=hash_password(password), enabled=1, must_change_password=1)
    audit(request, "user.create", name)
    return public_user(created)


@router.put("/api/users/{id}")
async def update_user(id: int, request: Request, ctx: Ctx, actor: User, data: dict = Body(...)):
    target = user_or_404(ctx.store, id)
    role, enabled = data.get("role", target["role"]), data.get("enabled", bool(target["enabled"]))
    if not isinstance(role, str) or role not in ROLES or not isinstance(enabled, bool):
        raise ValueError("角色或启用状态无效")
    if (role == "admin" or administrator_account(ctx.store, target)) and not has_global_permission(ctx.store, actor, "platform.admin"):
        raise Forbidden("只有平台管理员可以管理管理员账号")
    protect_user(ctx.store, actor, target, role, enabled)
    result = ctx.store.update_user(id, display_name=text_field(data, "display_name") or target["username"],
                                   email=text_field(data, "email", 254), role=role, enabled=int(enabled))
    if role != target["role"]:
        ctx.store.reset_user_role_bindings(id, role, actor["id"])
    if not enabled or role != target["role"]:
        ctx.store.delete_user_sessions(id)
        ctx.store.delete_user_api_tokens(id)
    audit(request, "user.update", target["username"])
    return public_user(result)


@router.delete("/api/users/{id}")
async def delete_user(id: int, request: Request, ctx: Ctx, actor: User):
    target = user_or_404(ctx.store, id)
    if administrator_account(ctx.store, target) and not has_global_permission(ctx.store, actor, "platform.admin"):
        raise Forbidden("只有平台管理员可以管理管理员账号")
    protect_user(ctx.store, actor, target, target["role"], target["enabled"], True)
    ctx.store.delete_user(id)
    audit(request, "user.delete", target["username"])
    return {"ok": True}


@router.post("/api/users/{id}/reset-password")
async def reset_password(id: int, request: Request, ctx: Ctx, actor: User, data: dict = Body(default={})):
    target = user_or_404(ctx.store, id)
    if administrator_account(ctx.store, target) and not has_global_permission(ctx.store, actor, "platform.admin"):
        raise Forbidden("只有平台管理员可以重置管理员密码")
    if id == actor["id"]:
        raise ValueError("请在“我的账号”修改自己的密码")
    password = data.get("password")
    if password is not None and not isinstance(password, str):
        raise ValueError("密码格式无效")
    result = ctx.auth.reset_password(id, password)
    ctx.auth.unlock(target["username"])
    audit(request, "user.reset_password", target["username"])
    return {"password": result, "must_change_password": True}
