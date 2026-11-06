"""成员授权。"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Body, Request, Response

from ..authz import has_global_permission, visible_namespace_ids, visible_project_ids
from ..errors import Conflict, DomainError, Forbidden, NotFound
from ..services.bindings import REQUEST_FIELDS, binding_view, can_manage, check_grant, parse_expiry
from ..validation import reject_unknown, text_field
from ..web.deps import Ctx, User, audit

router = APIRouter()


@router.get("/api/bindings")
async def list_bindings(ctx: Ctx, user: User):
    """平台管理员看到全部授权；项目/命名空间成员管理员只看到自己管理范围内的授权。"""
    rows = ctx.store.all_bindings()
    if not has_global_permission(ctx.store, user, "user.manage"):
        projects = set(visible_project_ids(ctx.store, user, "project.member.manage"))
        namespaces = set(visible_namespace_ids(ctx.store, user, "namespace.member.manage"))
        rows = [b for b in rows if (b["scope_type"] == "project" and b["scope_id"] in projects)
                or (b["scope_type"] == "namespace" and b["scope_id"] in namespaces)]
    items = [binding_view(row) for row in rows]
    return {"items": items, "total": len(items)}


@router.get("/api/bindable-users")
async def bindable_users(ctx: Ctx, user: User):
    store = ctx.store
    if (not has_global_permission(store, user, "user.manage")
            and not visible_project_ids(store, user, "project.member.manage")
            and not visible_namespace_ids(store, user, "namespace.member.manage")):
        raise Forbidden("没有任何可管理的成员范围")
    items = [{"id": row["id"], "username": row["username"], "display_name": row.get("display_name") or row["username"],
              "enabled": bool(row["enabled"])} for row in store.list_users() if row["enabled"]]
    return {"items": items, "total": len(items)}


@router.post("/api/bindings", status_code=201)
async def create_binding(request: Request, ctx: Ctx, user: User, data: dict = Body(...)):
    reject_unknown(data, REQUEST_FIELDS, "授权请求包含未知字段")
    if data.get("subject_type") != "user":
        raise ValueError("当前只支持直接绑定用户")
    subject_id, scope_id = data.get("subject_id"), data.get("scope_id", 0)
    scope_type, role = data.get("scope_type"), data.get("role")
    if type(subject_id) is not int or not ctx.store.get_user(subject_id):
        raise ValueError("用户不存在")
    if type(scope_id) is not int:
        raise ValueError("scope_id 格式错误")
    scope_id = check_grant(ctx.store, user, scope_type, scope_id, role)
    expires = parse_expiry(data.get("expires_at"))
    try:
        binding_id = ctx.store.create_binding("user", subject_id, role, scope_type, scope_id, user["id"], expires,
                                              text_field(data, "note", 200))
    except sqlite3.IntegrityError as exc:
        if "UNIQUE constraint failed: role_bindings" in str(exc):
            raise Conflict("该用户已拥有相同范围和角色") from None
        raise
    audit(request, "binding.create", binding_id, f"{scope_type}:{scope_id} role={role}")
    return binding_view(ctx.store.get_binding(binding_id))


@router.delete("/api/bindings/{id}", status_code=204)
async def delete_binding(id: int, request: Request, ctx: Ctx, user: User):
    binding = ctx.store.get_binding(id)
    if not binding or not can_manage(ctx.store, user, binding):
        raise NotFound("授权不存在")
    if (binding["scope_type"] == "global" and binding["role"] == "platform_admin"
            and not ctx.store.count_platform_admins(binding["subject_id"])):
        raise DomainError("至少保留一名平台管理员")
    ctx.store.delete_binding(id)
    audit(request, "binding.delete", id)
    return Response(status_code=204)
