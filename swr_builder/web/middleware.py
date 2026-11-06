"""认证、CSRF、权限、安全响应头与通用审计中间件。

处理顺序与 v1.2 一致：
1. authenticate        公开路径放行；解析 Bearer 令牌或会话 Cookie；未登录 API 返回 401，页面 303 到登录页
2. check_request_guard  Cookie 会话的写请求必须带 X-Requested-With；待改密账号只能访问改密相关接口
3. authorize           路由权限表 + 全局/作用域权限 + 部署模块开关
4. finalize            安全响应头、缓存策略、通用审计
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Union
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from ..auth import SESSION_COOKIE, has_perm
from ..authz import has_global_permission, visible_namespace_ids, visible_project_ids
from ..context import AppContext
from .permissions import AUTHENTICATED_ROUTE, PUBLIC_ROUTE, SCOPED_PERMISSIONS, route_permission

PUBLIC_PATHS = ("/login", "/healthz", "/favicon.ico")
PUBLIC_PREFIXES = ("/assets/",)
PASSWORD_CHANGE_ALLOWED = ("/api/auth/me", "/api/auth/logout", "/api/account/password")
# 这些接口在处理函数里写了更具体的审计（或本身是登录类操作），不再补记通用审计
AUDIT_EXCLUDED_PREFIXES = ("/api/auth/", "/api/account/", "/api/users", "/api/settings", "/api/deployments")
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")

CONTENT_SECURITY_POLICY = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                           "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; "
                           "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


def _deny(message: str, status: int = 403, code: str = "FORBIDDEN") -> JSONResponse:
    return JSONResponse({"detail": message, "code": code}, status_code=status)


def authenticate(ctx: AppContext, request: Request) -> Union[Dict[str, Any], Response]:
    header = request.headers.get("authorization", "")
    user = None
    if header.lower().startswith("bearer "):
        user = ctx.auth.resolve_api_token(header[7:].strip())
    else:
        resolved = ctx.auth.resolve_session(request.cookies.get(SESSION_COOKIE, ""))
        if resolved:
            user, request.state.session_hash = resolved
    if user:
        return user
    path = request.url.path
    if path.startswith("/api/"):
        return _deny("请先登录", 401, "UNAUTHENTICATED")
    target = path + ("?" + request.url.query if request.url.query else "")
    return RedirectResponse("/login?next=" + quote(target, safe=""), status_code=303)


def check_request_guard(request: Request, user: Dict[str, Any]) -> Optional[Response]:
    path = request.url.path
    if (request.state.session_hash and request.method not in SAFE_METHODS
            and request.headers.get("X-Requested-With") != "XMLHttpRequest"):
        return _deny("缺少安全请求头，请从平台页面操作", code="CSRF_HEADER_REQUIRED")
    if user["must_change_password"] and path.startswith("/api/") and path not in PASSWORD_CHANGE_ALLOWED:
        return _deny("首次登录或重置密码后，请先修改密码", code="PASSWORD_CHANGE_REQUIRED")
    return None


def authorize(ctx: AppContext, request: Request, user: Dict[str, Any], permission: Optional[str]) -> Optional[Response]:
    path = request.url.path
    if path.startswith("/api/"):
        if permission is None:
            return _deny("API 路由未配置权限，已拒绝访问", code="ROUTE_NOT_REGISTERED")
        if permission not in (PUBLIC_ROUTE, AUTHENTICATED_ROUTE):
            store = ctx.store
            global_scope = has_global_permission(store, user, permission)
            if permission in SCOPED_PERMISSIONS:
                if not global_scope:
                    visible = (visible_namespace_ids(store, user, permission) if permission == "namespace.view"
                               else visible_project_ids(store, user, permission))
                    if not visible:
                        return _deny("没有可访问的项目资源")
            elif not has_perm(user["role"], permission) and not global_scope:
                return _deny("没有操作权限")
    if not ctx.settings.general.deploy_enabled and path.startswith(("/api/deploy/", "/api/deployments")):
        return _deny("部署模块已关闭", code="MODULE_DISABLED")
    return None


def finalize(ctx: AppContext, request: Request, response: Response, user: Optional[Dict[str, Any]]) -> Response:
    path = request.url.path
    headers = response.headers
    headers["X-Content-Type-Options"] = "nosniff"
    headers["X-Frame-Options"] = "DENY"
    headers["Referrer-Policy"] = "same-origin"
    headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    if ctx.settings.server.secure_cookie:
        headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    if path.startswith("/api/"):
        headers["Cache-Control"] = "no-store"
    elif path.startswith("/assets/") and response.status_code == 200:
        headers["Cache-Control"] = "public, max-age=31536000, immutable"
    if (user and request.method not in SAFE_METHODS and response.status_code < 400
            and not getattr(request.state, "audited", False) and not path.startswith(AUDIT_EXCLUDED_PREFIXES)):
        ctx.store.add_audit(user["username"], request.method + " " + path,
                            ip=request.client.host if request.client else "")
    return response


def install(app: FastAPI, ctx: AppContext) -> None:
    @app.middleware("http")
    async def security(request: Request, call_next):
        path = request.url.path
        permission = route_permission(app, path, request.method) if path.startswith("/api/") else None
        request.state.session_hash = None
        user = None
        public = path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES) or permission == PUBLIC_ROUTE
        if not public:
            resolved = authenticate(ctx, request)
            if isinstance(resolved, Response):
                return finalize(ctx, request, resolved, None)
            user = request.state.user = resolved
            denied = check_request_guard(request, user) or authorize(ctx, request, user, permission)
            if denied:
                return finalize(ctx, request, denied, None)
        response = await call_next(request)
        return finalize(ctx, request, response, user)
