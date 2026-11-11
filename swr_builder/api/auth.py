"""登录、会话与"我的账号"。"""
from __future__ import annotations

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse, Response

from .. import __version__
from ..auth import SESSION_COOKIE, AuthError
from ..errors import Forbidden, NotFound
from ..services import accounts
from ..validation import text_field
from ..web.deps import Ctx, Me, User, audit, authenticated, client_ip, public, session_hash

router = APIRouter()


@router.get("/api/auth/config")
@public
async def auth_config(ctx: Ctx):
    return {"site_name": ctx.settings.general.site_name, "version": __version__}


@router.post("/api/auth/login")
@public
async def login(request: Request, ctx: Ctx, data: dict = Body(...)):
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        raise Forbidden("请从登录页面提交", code="CSRF_HEADER_REQUIRED")
    name = text_field(data, "username", 32)
    password = data.get("password", "")
    if not isinstance(password, str) or len(password) > 128:
        raise ValueError("密码格式无效")
    ip = client_ip(request)
    try:
        user = ctx.auth.authenticate(name, password, ip)
    except AuthError:
        ctx.store.add_audit(name, "auth.login_failed", ip=ip)
        raise
    remember = data.get("remember", False) is True
    token, seconds = ctx.auth.create_session(user["id"], remember, ip, request.headers.get("user-agent", ""))
    response = JSONResponse(accounts.view_of(ctx.store, user))
    response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=ctx.settings.server.secure_cookie,
                        samesite="lax", max_age=seconds if remember else None, path="/")
    ctx.store.add_audit(user["username"], "auth.login", ip=ip)
    return response


@router.get("/api/auth/me")
@authenticated
async def auth_me(ctx: Ctx, me: Me):
    return accounts.session_profile(ctx.store, ctx.settings, me)


@router.post("/api/auth/logout")
@authenticated
async def logout(request: Request, ctx: Ctx):
    ctx.auth.logout(session_hash(request))
    audit(request, "auth.logout")
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE, path="/", secure=ctx.settings.server.secure_cookie, httponly=True,
                           samesite="lax")
    return response


@router.put("/api/account/profile")
@authenticated
async def update_profile(request: Request, ctx: Ctx, user: User, data: dict = Body(...)):
    updated = ctx.store.update_user(user["id"], display_name=text_field(data, "display_name") or user["username"],
                                    email=text_field(data, "email", 254))
    audit(request, "account.profile")
    return accounts.view_of(ctx.store, updated)


@router.put("/api/account/password")
@authenticated
async def change_password(request: Request, ctx: Ctx, user: User, data: dict = Body(...)):
    old, new = data.get("old_password", ""), data.get("new_password", "")
    if not isinstance(old, str) or not isinstance(new, str) or len(old) > 128 or len(new) > 128:
        raise ValueError("密码格式无效")
    ctx.auth.change_password(user, old, new, session_hash(request))
    audit(request, "account.password")
    return {"ok": True}


@router.get("/api/account/sessions")
@authenticated
async def list_sessions(request: Request, ctx: Ctx, user: User):
    current = session_hash(request)
    items = [{"id": row["token_hash"], "ip": row["ip"], "user_agent": row["user_agent"],
              "created_at": row["created_at"], "last_seen_at": row["last_seen_at"], "expires_at": row["expires_at"],
              "current": row["token_hash"] == current}
             for row in ctx.store.list_sessions(user["id"])]
    return {"items": items, "total": len(items)}


@router.delete("/api/account/sessions/{id}", status_code=204)
@authenticated
async def revoke_session(id: str, request: Request, ctx: Ctx, user: User):
    row = ctx.store.get_session(id)
    if not row or row["user_id"] != user["id"]:
        raise NotFound("会话不存在")
    ctx.store.delete_session(id)
    audit(request, "account.session_revoke")
    return Response(status_code=204)


@router.get("/api/account/tokens")
@authenticated
async def list_tokens(ctx: Ctx, user: User):
    items = ctx.store.list_api_tokens(user["id"])
    return {"items": items, "total": len(items)}


@router.post("/api/account/tokens", status_code=201)
@authenticated
async def create_token(request: Request, ctx: Ctx, user: User, data: dict = Body(...)):
    if not session_hash(request):
        raise Forbidden("请使用网页登录创建令牌")
    name = text_field(data, "name")
    if not name:
        raise ValueError("请填写令牌名称")
    days = data.get("expires_days", 30)
    if type(days) is not int or not 1 <= days <= 365:
        raise ValueError("有效期为 1–365 天")
    token, token_id = ctx.auth.create_api_token(user["id"], name, days)
    audit(request, "account.token_create", token_id, f"name={name};days={days}")
    return {"id": token_id, "token": token}


@router.delete("/api/account/tokens/{id}", status_code=204)
@authenticated
async def revoke_token(id: int, request: Request, ctx: Ctx, user: User):
    if not ctx.store.delete_api_token(id, user["id"]):
        raise NotFound("令牌不存在")
    audit(request, "account.token_revoke", id)
    return Response(status_code=204)
