"""业务错误类型。

服务层抛出 DomainError（或子类），由全局异常处理器统一转换为
`{"detail": "<中文说明>", "code": "<机器可读代码>"}`，HTTP 状态码取 `status`。
"""
from __future__ import annotations

from typing import Optional


class DomainError(Exception):
    status = 400
    code = "BAD_REQUEST"

    def __init__(self, message: str, *, status: Optional[int] = None, code: Optional[str] = None):
        super().__init__(message)
        self.message = message
        if status is not None:
            self.status = status
        if code is not None:
            self.code = code


class NotFound(DomainError):
    status, code = 404, "NOT_FOUND"


class Forbidden(DomainError):
    status, code = 403, "FORBIDDEN"


class Conflict(DomainError):
    status, code = 409, "CONFLICT"


class Unprocessable(DomainError):
    status, code = 422, "UNPROCESSABLE"


class NotImplementedYet(DomainError):
    status, code = 501, "NOT_IMPLEMENTED"


class UpstreamError(DomainError):
    """SWR / Kubernetes / IAM 等外部系统返回错误。"""
    status, code = 502, "UPSTREAM_ERROR"


class Unavailable(DomainError):
    """外部系统不可达。"""
    status, code = 503, "UPSTREAM_UNAVAILABLE"


class RevisionConflict(Conflict, RuntimeError):
    """服务配置修订的乐观锁冲突。继承 RuntimeError 以兼容旧的调用方。"""
    code = "REVISION_CONFLICT"

    def __init__(self, message: str = "REVISION_CONFLICT"):
        super().__init__(message)
