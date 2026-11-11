"""按作用域权限读取资源。用户无权访问的资源一律按"不存在"处理（返回 404），不泄露其存在性。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..access import Principal
from ..errors import NotFound
from ..store import Store


def app_or_404(store: Store, me: Optional[Principal], app_id: int, permission: str = "app.view") -> Dict[str, Any]:
    data = store.get_app(app_id)
    if not data or (me is not None and not me.can_project(data["project_id"], permission)):
        raise NotFound("应用不存在")
    return data


def job_or_404(store: Store, me: Optional[Principal], job_id: int) -> Dict[str, Any]:
    job = store.get_job(job_id)
    if not job or (me is not None and not me.can_project(job["project_id"], "job.view")):
        raise NotFound("构建任务不存在")
    return job


def project_or_404(store: Store, me: Principal, project_id: int, permission: str = "project.view") -> Dict[str, Any]:
    project = store.get_project(project_id)
    if not project or not me.can_project(project_id, permission):
        raise NotFound("项目不存在")
    return project


def namespace_or_404(store: Store, me: Principal, namespace_id: int,
                     permission: str = "namespace.view") -> Dict[str, Any]:
    namespace = store.get_namespace(namespace_id)
    if not namespace or not me.can_namespace(namespace_id, permission):
        raise NotFound("命名空间不存在")
    return namespace


def service_or_404(store: Store, me: Principal, service_id: int, permission: Optional[str] = None,
                   allow_project_view: bool = True) -> Dict[str, Any]:
    """命名空间上有 permission（默认 namespace.view），或（allow_project_view 时）项目上有 app.view 即可读取。"""
    service = store.get_service(service_id)
    if not service:
        raise NotFound("服务不存在")
    namespace_ok = me.can_namespace(service["namespace_id"], permission or "namespace.view")
    project_ok = allow_project_view and me.can_project(service["project_id"], "app.view")
    if not namespace_ok and not project_ok:
        raise NotFound("服务不存在")
    return service


def user_or_404(store: Store, user_id: int) -> Dict[str, Any]:
    found = store.get_user(user_id)
    if not found:
        raise NotFound("账号不存在")
    return found
