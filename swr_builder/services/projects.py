"""项目字段校验。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..apps import ORG_RE, REPO_RE
from ..errors import DomainError
from ..validation import DNS_LABEL_RE, reject_unknown, text_field

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


def project_fields(data: Dict[str, Any], current_slug: Optional[str] = None) -> Tuple[str, str, List[Dict[str, Any]]]:
    """校验项目请求体，返回 (slug, name, registry_orgs)。current_slug 为修改前的 slug（新建时为 None）。"""
    slug, name = text_field(data, "slug", 63).lower(), text_field(data, "name", 64)
    if current_slug == "default" and slug != "default":
        raise DomainError("默认项目 slug 不能修改")
    if not DNS_LABEL_RE.fullmatch(slug):
        raise ValueError("项目 slug 必须是 DNS-1123 label")
    if not name:
        raise ValueError("请填写项目名称")
    return slug, name, normalize_registry_orgs(data.get("registry_orgs", []))


def check_project_payload(data: Dict[str, Any]) -> None:
    reject_unknown(data, PROJECT_FIELDS, "项目请求包含未知字段")
