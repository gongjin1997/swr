"""资源管理的业务逻辑层（权限检查、公开字段、CRUD）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..access import Principal
from ..delivery import resources as resource_types
from ..errors import Conflict, Forbidden, NotFound
from ..security import SecretBox
from ..store import Store
from ..validation import DNS_SUBDOMAIN_RE, reject_unknown, text_field


def _can_view(me: Principal, scope_type: str, scope_id: int) -> bool:
    if scope_type == "platform":
        return me.can_anywhere("resource.view") or me.can_anywhere("resource.manage")
    return me.can_namespace(scope_id, "resource.view") or me.can_namespace(scope_id, "resource.manage")


def _can_manage(me: Principal, scope_type: str, scope_id: int) -> bool:
    if scope_type == "platform":
        return me.can("resource.manage")
    return me.can_namespace(scope_id, "resource.manage")


def _can_reveal(me: Principal, scope_type: str, scope_id: int) -> bool:
    if scope_type == "platform":
        return me.can("secret.view")
    return me.can_namespace(scope_id, "secret.view")


def resource_or_404(store: Store, me: Principal, resource_id: int, permission: Optional[str] = None
                   ) -> Dict[str, Any]:
    resource = store.get_resource(resource_id)
    if not resource:
        raise NotFound("资源不存在")
    if not _can_view(me, resource["scope_type"], resource["scope_id"]):
        raise NotFound("资源不存在")
    if permission == "manage" and not _can_manage(me, resource["scope_type"], resource["scope_id"]):
        raise Forbidden("没有管理该资源的权限")
    elif permission == "reveal" and not _can_reveal(me, resource["scope_type"], resource["scope_id"]):
        raise Forbidden("没有查看敏感配置的权限")
    return resource


def public_resource(store: Store, box: SecretBox, me: Principal, resource: Dict[str, Any]) -> Dict[str, Any]:
    sizes = resource_types.secret_size(resource["type"], resource.get("attributes") or {}, box)
    return {
        "id": resource["id"],
        "scope_type": resource["scope_type"],
        "scope_id": resource["scope_id"],
        "type": resource["type"],
        "name": resource["name"],
        "description": resource["description"],
        "parent_id": resource.get("parent_id"),
        "version": resource.get("version") or 0,
        "attributes": resource_types.mask_secrets(resource["type"], resource.get("attributes") or {}, sizes),
        "created_at": resource["created_at"],
        "updated_at": resource["updated_at"],
        "binding_count": resource.get("binding_count") or 0,
        "child_count": resource.get("child_count") or 0,
        "can_edit": _can_manage(me, resource["scope_type"], resource["scope_id"]),
        "can_reveal": _can_reveal(me, resource["scope_type"], resource["scope_id"]),
    }


def list_resources(store: Store, box: SecretBox, me: Principal, scope_type: Optional[str] = None,
                  scope_id: Optional[int] = None, type_name: Optional[str] = None, limit: int = 100, offset: int = 0
                  ) -> Tuple[List[Dict[str, Any]], int]:
    items, total = store.list_resources(scope_type, scope_id, type_name, limit, offset)
    visible = []
    for resource in items:
        if _can_view(me, resource["scope_type"], resource["scope_id"]):
            visible.append(public_resource(store, box, me, resource))
    return visible, len(visible)


def get_resource(store: Store, box: SecretBox, me: Principal, resource_id: int) -> Dict[str, Any]:
    resource = resource_or_404(store, me, resource_id)
    return public_resource(store, box, me, resource)


def create_resource(store: Store, box: SecretBox, me: Principal, data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("请求体必须是对象")
    reject_unknown(data, ("scope_type", "scope_id", "type", "name", "description", "attributes", "note", "parent_id"),
                   "包含不支持的字段")
    scope_type = data.get("scope_type")
    if scope_type not in ("platform", "environment"):
        raise ValueError("scope_type 必须是 platform 或 environment")
    scope_id = data.get("scope_id", 0)
    if scope_type == "platform":
        scope_id = 0
    elif not isinstance(scope_id, int) or scope_id <= 0:
        raise ValueError("environment scope 需要有效的 scope_id")
    if not _can_manage(me, scope_type, scope_id):
        raise Forbidden("没有在该层级创建资源的权限")
    if scope_type == "environment":
        namespace = store.get_namespace(scope_id)
        if not namespace:
            raise NotFound("环境不存在")
    type_name = data.get("type")
    resource_types.validate_type(type_name)
    spec = resource_types.RESOURCE_TYPES[type_name]
    parent_id = data.get("parent_id")
    if spec.get("parent"):
        if not parent_id:
            raise ValueError(f"{spec['label']} 需要指定父资源")
        parent = store.get_resource(parent_id)
        if not parent:
            raise NotFound("父资源不存在")
        if parent["type"] != spec["parent"]:
            raise ValueError(f"{spec['label']} 的父资源必须是 {resource_types.RESOURCE_TYPES[spec['parent']]['label']}")
        if scope_type == "environment" and parent["scope_type"] == "environment" and parent["scope_id"] != scope_id:
            raise ValueError("环境资源的环境父资源必须在同一环境")
        if scope_type == "platform" and parent["scope_type"] != "platform":
            raise ValueError("平台资源的父资源必须是平台资源")
    elif parent_id:
        raise ValueError(f"{spec['label']} 不需要父资源")
    name = text_field(data, "name", 253)
    if not name or not DNS_SUBDOMAIN_RE.fullmatch(name):
        raise ValueError("资源名称必须是 DNS 子域名格式")
    description = text_field(data, "description", 300)
    attributes = data.get("attributes", {})
    if not isinstance(attributes, dict):
        raise ValueError("attributes 必须是对象")
    validated_attrs = resource_types.validate_attributes(type_name, attributes, None, box)
    checksum = resource_types.checksum(validated_attrs)
    note = text_field(data, "note", 300)
    resource_id = store.create_resource(scope_type, scope_id, type_name, name, description, validated_attrs, checksum,
                                       note, me.id, parent_id)
    return get_resource(store, box, me, resource_id)


def update_resource(store: Store, box: SecretBox, me: Principal, resource_id: int, data: Any) -> Dict[str, Any]:
    resource = resource_or_404(store, me, resource_id, "manage")
    if not isinstance(data, dict):
        raise ValueError("请求体必须是对象")
    reject_unknown(data, ("base_version", "attributes", "note", "description"), "包含不支持的字段")
    base_version = data.get("base_version")
    if not isinstance(base_version, int) or isinstance(base_version, bool):
        raise ValueError("base_version 必须是整数")
    attributes = data.get("attributes", {})
    if not isinstance(attributes, dict):
        raise ValueError("attributes 必须是对象")
    previous = resource.get("attributes") or {}
    validated_attrs = resource_types.validate_attributes(resource["type"], attributes, previous, box)
    checksum = resource_types.checksum(validated_attrs)
    note = text_field(data, "note", 300)
    description = text_field(data, "description", 300) if "description" in data else None
    store.add_resource_version(resource_id, base_version, validated_attrs, checksum, note, me.id, description)
    return get_resource(store, box, me, resource_id)


def delete_resource(store: Store, me: Principal, resource_id: int) -> None:
    resource = resource_or_404(store, me, resource_id, "manage")
    if resource.get("child_count", 0) > 0:
        raise Conflict("该资源有子资源，不能删除")
    if resource.get("binding_count", 0) > 0:
        # 和 XAC 一样，删除被拒时直接告诉用户是谁在用，省得自己去翻
        users = store.list_resource_bindings_by_resource(resource_id)
        names = "、".join(f"{u['namespace_name']}/{u['service_name']}" for u in users[:5])
        more = f" 等 {len(users)} 个服务" if len(users) > 5 else ""
        raise Conflict(f"资源被 {names}{more} 绑定，不能删除；请先在这些服务中解绑")
    store.delete_resource(resource_id)


def reveal_resource(store: Store, box: SecretBox, me: Principal, resource_id: int) -> Dict[str, Any]:
    resource = resource_or_404(store, me, resource_id, "reveal")
    attributes = resource.get("attributes") or {}
    decrypted = {}
    for key, value in attributes.items():
        if key in resource_types.secret_attributes(resource["type"]):
            try:
                decrypted[key] = box.decrypt(value) if value and value.startswith("enc:") else ""
            except Exception:
                decrypted[key] = "[解密失败]"
        else:
            decrypted[key] = value
    return {"id": resource["id"], "type": resource["type"], "name": resource["name"],
            "version": resource.get("version") or 0, "attributes": decrypted}


def list_resource_versions(store: Store, me: Principal, resource_id: int) -> List[Dict[str, Any]]:
    resource_or_404(store, me, resource_id)
    return store.list_resource_versions(resource_id)


def get_resource_version(store: Store, box: SecretBox, me: Principal, resource_id: int, version: int
                        ) -> Dict[str, Any]:
    resource_or_404(store, me, resource_id)
    version_data = store.get_resource_version(resource_id, version)
    if not version_data:
        raise NotFound("资源版本不存在")
    resource = store.get_resource(resource_id)
    sizes = resource_types.secret_size(resource["type"], version_data.get("attributes") or {}, box)
    return {
        "resource_id": resource_id,
        "number": version_data["number"],
        "attributes": resource_types.mask_secrets(resource["type"], version_data.get("attributes") or {}, sizes),
        "checksum": version_data["checksum"],
        "note": version_data.get("note", ""),
        "created_at": version_data["created_at"],
        "created_by": version_data.get("created_by"),
    }


def list_service_bindings(store: Store, box: SecretBox, me: Principal, service_id: int) -> List[Dict[str, Any]]:
    from .access import service_or_404
    service_or_404(store, me, service_id)
    bindings = store.list_service_resource_bindings(service_id)
    result = []
    for binding in bindings:
        resource = binding["resource"]
        sizes = resource_types.secret_size(resource["type"], resource.get("attributes") or {}, box)
        result.append({
            "alias": binding["alias"],
            "resource_id": binding["resource_id"],
            "resource": {
                "id": resource["id"],
                "type": resource["type"],
                "name": resource["name"],
                "scope_type": resource["scope_type"],
                "scope_id": resource["scope_id"],
                "parent_id": resource.get("parent_id"),
                "version": resource.get("version") or 0,
                "attributes": resource_types.mask_secrets(resource["type"], resource.get("attributes") or {}, sizes),
            }
        })
    return result


def bind_resource(store: Store, box: SecretBox, me: Principal, service_id: int, data: Any) -> Dict[str, Any]:
    from .access import service_or_404
    from .catalog import mode_of, writable_service
    service = writable_service(store, me, service_id, "service.config", "没有配置服务的权限")
    if mode_of(service) != "managed":
        raise Forbidden("只有托管服务可以绑定资源")
    if not isinstance(data, dict):
        raise ValueError("请求体必须是对象")
    reject_unknown(data, ("resource_id", "alias"), "包含不支持的字段")
    resource_id = data.get("resource_id")
    if not isinstance(resource_id, int):
        raise ValueError("resource_id 必须是整数")
    alias = text_field(data, "alias", 63)
    resource_types.validate_alias(alias)
    resource = store.get_resource(resource_id)
    if not resource:
        raise NotFound("资源不存在")
    if not _can_view(me, resource["scope_type"], resource["scope_id"]):
        raise NotFound("资源不存在")
    if resource["scope_type"] == "environment" and resource["scope_id"] != service["namespace_id"]:
        raise Forbidden("只能绑定本环境或平台的资源")
    store.create_service_resource_binding(service_id, resource_id, alias, me.id)
    return {"alias": alias, "resource_id": resource_id}


def unbind_resource(store: Store, me: Principal, service_id: int, resource_id: int) -> None:
    from .catalog import writable_service
    writable_service(store, me, service_id, "service.config", "没有配置服务的权限")
    if not store.delete_service_resource_binding(service_id, resource_id):
        raise NotFound("绑定不存在")
