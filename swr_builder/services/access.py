"""按作用域权限读取资源。用户无权访问的资源一律按"不存在"处理（返回 404），不泄露其存在性。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..authz import has_namespace_permission, has_project_permission
from ..errors import NotFound
from ..store import Store

User = Dict[str, Any]


def app_or_404(store: Store, user: Optional[User], app_id: int, permission: str = "app.view") -> Dict[str, Any]:
    data = store.get_app(app_id)
    if not data or (user is not None and not has_project_permission(store, user, data["project_id"], permission)):
        raise NotFound("应用不存在")
    return data


def job_or_404(store: Store, user: Optional[User], job_id: int) -> Dict[str, Any]:
    job = store.get_job(job_id)
    if not job or (user is not None and not has_project_permission(store, user, job["project_id"], "job.view")):
        raise NotFound("构建任务不存在")
    return job


def project_or_404(store: Store, user: User, project_id: int, permission: str = "project.view") -> Dict[str, Any]:
    project = store.get_project(project_id)
    if not project or not has_project_permission(store, user, project_id, permission):
        raise NotFound("项目不存在")
    return project


def service_or_404(store: Store, user: User, service_id: int, permission: Optional[str] = None,
                   allow_project_view: bool = True) -> Dict[str, Any]:
    """命名空间上有 permission（默认 namespace.view），或（allow_project_view 时）项目上有 app.view 即可读取。"""
    service = store.get_service(service_id)
    if not service:
        raise NotFound("服务不存在")
    namespace_ok = has_namespace_permission(store, user, service["namespace_id"], permission or "namespace.view")
    project_ok = allow_project_view and has_project_permission(store, user, service["project_id"], "app.view")
    if not namespace_ok and not project_ok:
        raise NotFound("服务不存在")
    return service


def user_or_404(store: Store, user_id: int) -> Dict[str, Any]:
    found = store.get_user(user_id)
    if not found:
        raise NotFound("账号不存在")
    return found
