"""路由依赖与请求辅助函数。"""
from __future__ import annotations

from typing import Any, Dict

from typing import Annotated

from fastapi import Depends, Request

from ..context import AppContext


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def get_user(request: Request) -> Dict[str, Any]:
    """当前登录用户（由认证中间件写入 request.state.user）。"""
    return request.state.user


Ctx = Annotated[AppContext, Depends(get_ctx)]
User = Annotated[Dict[str, Any], Depends(get_user)]


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def display_name(user: Dict[str, Any]) -> str:
    return user.get("display_name") or user["username"]


def session_hash(request: Request):
    return getattr(request.state, "session_hash", None)


def audit(request: Request, action: str, target: Any = "", detail: str = "") -> None:
    """记录一条业务审计。写过业务审计的请求，中间件不再补记通用审计。"""
    request.app.state.ctx.store.add_audit(request.state.user["username"], action, str(target), detail,
                                          client_ip(request))
    request.state.audited = True


