"""项目：字段校验、SWR 授权、命名空间分配。

项目是平台的组织单位：应用、构建、服务、发布记录都属于某个项目；项目成员通过项目范围的授权管理。
新建/修改/删除项目与分配命名空间需要全局 project.manage。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..access import Principal
from ..apps import ORG_RE, REPO_RE
from ..errors import Conflict, DomainError, NotFound
from ..store import Store
from ..validation import DNS_LABEL_RE, reject_unknown, require_empty, text_field
from .access import project_or_404

PROJECT_FIELDS = {"slug", "name", "registry_orgs"}


def normalize_registry_orgs(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("registry_orgs 必须是数组")
    normalized, seen = [], set()
    for item in value:
        if not isinstance(item, dict) or set(item) - {"namespace", "base_prefixes"}:
            raise ValueError("镜像组织配置格式错误")
        namespace, prefixes = item.get("namespace"), item.get("base_prefixes", [])
        if not isinstance(namespace, str) or not ORG_RE.fullmatch(namespace):
            raise ValueError("SWR 组织名称无效")
        if namespace in seen:
            raise ValueError("SWR 组织不能重复")
        seen.add(namespace)
        if not isinstance(prefixes, list) or any(not isinstance(p, str) or not REPO_RE.fullmatch(p) for p in prefixes):
            raise ValueError("镜像仓库前缀无效")
        normalized.append({"namespace": namespace, "base_prefixes": sorted(set(prefixes))})
    return normalized


def project_fields(data: Any, current_slug: Optional[str] = None) -> Tuple[str, str, List[Dict[str, Any]]]:
    """校验项目请求体，返回 (slug, name, registry_orgs)。current_slug 为修改前的 slug（新建时为 None）。"""
    if not isinstance(data, dict):
        raise ValueError("项目请求格式错误")
    reject_unknown(data, PROJECT_FIELDS, "项目请求包含未知字段")
    slug, name = text_field(data, "slug", 63).lower(), text_field(data, "name", 64)
    if current_slug == "default" and slug != "default":
        raise DomainError("默认项目 slug 不能修改")
    if not DNS_LABEL_RE.fullmatch(slug):
        raise ValueError("项目 slug 必须是 DNS-1123 label（小写字母、数字和 -）")
    if not name:
        raise ValueError("请填写项目名称")
    return slug, name, normalize_registry_orgs(data.get("registry_orgs", []))


def list_projects(store: Store, me: Principal) -> List[Dict[str, Any]]:
    ids = me.project_ids("project.view")
    return [p for p in store.list_projects() if ids is None or p["id"] in ids]


def create_project(store: Store, data: Any) -> Dict[str, Any]:
    slug, name, registry_orgs = project_fields(data)
    return store.create_project(slug, name, registry_orgs)


def update_project(store: Store, me: Principal, project_id: int, data: Any) -> Dict[str, Any]:
    current = project_or_404(store, me, project_id, "project.manage")
    slug, name, registry_orgs = project_fields(data, current["slug"])
    return store.update_project(project_id, slug, name, registry_orgs)  # type: ignore[return-value]


def delete_project(store: Store, me: Principal, project_id: int) -> None:
    project = project_or_404(store, me, project_id, "project.manage")
    if project["slug"] == "default":
        raise Conflict("默认项目不能删除")
    if not store.delete_project(project_id):
        raise Conflict("项目仍有关联应用、构建、发布记录或成员授权，不能删除")


def assign_namespace(store: Store, me: Principal, project_id: int, namespace_id: int, data: Any) -> Dict[str, Any]:
    require_empty(data or {})
    project_or_404(store, me, project_id, "project.manage")
    if not store.get_namespace(namespace_id):
        raise NotFound("命名空间不存在")
    store.assign_namespace(project_id, namespace_id)
    return store.get_project(project_id)  # type: ignore[return-value]


def unassign_namespace(store: Store, me: Principal, project_id: int, namespace_id: int) -> None:
    project_or_404(store, me, project_id, "project.manage")
    if not store.unassign_namespace(project_id, namespace_id):
        raise Conflict("命名空间未分配给该项目，或项目在其中仍有服务")
