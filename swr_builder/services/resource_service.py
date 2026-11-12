"""资源管理服务层（基于新的领域模型）。

提供资源管理的业务逻辑和权限检查。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..access.principal import Principal
from ..core.resource_exporter import export_resources
from ..core.resource_types import RESOURCE_TYPES, get_resource_type
from ..crypto import SecretBox
from ..store import Store


def can_view_resources(me: Principal, scope_type: str, scope_id: int) -> bool:
    """检查是否有权查看资源"""
    if scope_type == "platform":
        return me.has("resource.view")
    elif scope_type == "environment":
        return me.can_namespace(scope_id, "resource.view") or me.has("resource.view")
    return False


def can_manage_resources(me: Principal, scope_type: str, scope_id: int) -> bool:
    """检查是否有权管理资源"""
    if scope_type == "platform":
        return me.has("resource.manage")
    elif scope_type == "environment":
        return me.can_namespace(scope_id, "resource.manage") or me.has("resource.manage")
    return False


def list_resource_types() -> Dict[str, Any]:
    """列出所有资源类型"""
    items = []
    for code, rt in RESOURCE_TYPES.items():
        items.append({
            "code": rt.code,
            "label": rt.label,
            "parent_type": rt.parent_type,
            "attributes": [
                {
                    "name": attr.name,
                    "label": attr.label,
                    "type": attr.type,
                    "required": attr.required,
                    "default": attr.default,
                    "description": attr.description,
                }
                for attr in rt.attributes
            ],
            "export_rules": [
                {
                    "target_kind": rule.target_kind,
                    "template": rule.template,
                    "key": rule.key,
                }
                for rule in rt.export_rules
            ],
        })
    return {"items": items, "total": len(items)}


def list_resources(
    store: Store,
    me: Principal,
    scope_type: Optional[str] = None,
    scope_id: Optional[int] = None,
    resource_type: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    """列出资源"""
    # 权限检查
    if scope_type and scope_id is not None:
        if not can_view_resources(me, scope_type, scope_id):
            raise PermissionError("无权查看资源")
    elif not me.has("resource.view"):
        # 没有指定作用域，需要全局权限
        raise PermissionError("无权查看资源")

    items, total = store.list_resources(scope_type, scope_id, resource_type, None, limit, offset)
    return {"items": items, "total": total}


def get_resource(store: Store, me: Principal, resource_id: int) -> Dict[str, Any]:
    """获取资源详情"""
    resource = store.get_resource(resource_id)
    if not resource:
        raise ValueError("资源不存在")

    if not can_view_resources(me, resource["scope_type"], resource["scope_id"]):
        raise PermissionError("无权查看资源")

    return resource


def create_resource(
    store: Store,
    box: SecretBox,
    me: Principal,
    data: Dict[str, Any],
) -> Dict[str, Any]:
    """创建资源"""
    scope_type = data.get("scope_type")
    scope_id = data.get("scope_id", 0)
    resource_type = data.get("type")
    name = data.get("name", "").strip()
    attributes = data.get("attributes", {})
    parent_id = data.get("parent_id")
    description = data.get("description", "")

    if not scope_type or not resource_type or not name:
        raise ValueError("缺少必填字段")

    if not can_manage_resources(me, scope_type, scope_id):
        raise PermissionError("无权管理资源")

    # 验证资源类型
    type_def = get_resource_type(resource_type)
    if not type_def:
        raise ValueError(f"未知的资源类型: {resource_type}")

    # 加密 secret 类型的属性
    encrypted_attributes = {}
    for attr_def in type_def.attributes:
        attr_name = attr_def.name
        if attr_name in attributes:
            value = attributes[attr_name]
            if attr_def.type == "secret" and value:
                # 加密敏感值
                encrypted_attributes[attr_name] = box.encrypt(value)
            else:
                encrypted_attributes[attr_name] = value

    return store.create_resource(
        scope_type=scope_type,
        scope_id=scope_id,
        resource_type=resource_type,
        name=name,
        attributes=encrypted_attributes,
        parent_id=parent_id,
        description=description,
        created_by=me.user_id,
        note=data.get("note", ""),
    )


def update_resource(
    store: Store,
    box: SecretBox,
    me: Principal,
    resource_id: int,
    data: Dict[str, Any],
) -> Dict[str, Any]:
    """更新资源属性"""
    resource = store.get_resource(resource_id)
    if not resource:
        raise ValueError("资源不存在")

    if not can_manage_resources(me, resource["scope_type"], resource["scope_id"]):
        raise PermissionError("无权管理资源")

    attributes = data.get("attributes", {})
    type_def = get_resource_type(resource["type"])
    if not type_def:
        raise ValueError(f"未知的资源类型: {resource['type']}")

    # 加密 secret 类型的属性
    encrypted_attributes = {}
    for attr_def in type_def.attributes:
        attr_name = attr_def.name
        if attr_name in attributes:
            value = attributes[attr_name]
            if attr_def.type == "secret" and value:
                encrypted_attributes[attr_name] = box.encrypt(value)
            else:
                encrypted_attributes[attr_name] = value

    return store.update_resource_attributes(
        resource_id=resource_id,
        attributes=encrypted_attributes,
        created_by=me.user_id,
        note=data.get("note", ""),
    )


def delete_resource(store: Store, me: Principal, resource_id: int) -> None:
    """删除资源"""
    resource = store.get_resource(resource_id)
    if not resource:
        raise ValueError("资源不存在")

    if not can_manage_resources(me, resource["scope_type"], resource["scope_id"]):
        raise PermissionError("无权管理资源")

    store.delete_resource(resource_id)


def bind_resource_to_service(
    store: Store,
    me: Principal,
    service_id: int,
    resource_id: int,
    alias: str = "",
) -> Dict[str, Any]:
    """绑定资源到服务"""
    # 检查服务权限
    service = store.get_service(service_id)
    if not service:
        raise ValueError("服务不存在")

    if not me.can_namespace(service["namespace_id"], "service.config"):
        raise PermissionError("无权配置服务")

    # 检查资源是否存在
    resource = store.get_resource(resource_id)
    if not resource:
        raise ValueError("资源不存在")

    return store.bind_resource(service_id, resource_id, alias)


def list_service_bindings(store: Store, me: Principal, service_id: int) -> List[Dict[str, Any]]:
    """列出服务的资源绑定"""
    service = store.get_service(service_id)
    if not service:
        raise ValueError("服务不存在")

    if not me.can_namespace(service["namespace_id"], "service.view"):
        raise PermissionError("无权查看服务")

    return store.list_bindings(service_id)


def unbind_resource_from_service(store: Store, me: Principal, binding_id: int) -> None:
    """解绑资源"""
    binding = store.get_binding(binding_id)
    if not binding:
        raise ValueError("绑定不存在")

    service = store.get_service(binding["service_id"])
    if not service:
        raise ValueError("服务不存在")

    if not me.can_namespace(service["namespace_id"], "service.config"):
        raise PermissionError("无权配置服务")

    store.unbind_resource(binding_id)


def export_service_resources(
    store: Store,
    box: SecretBox,
    me: Principal,
    service_id: int,
    include_secrets: bool = False,
) -> Dict[str, Any]:
    """
    导出服务绑定的资源为配置。

    Returns:
        {
            "env": {key: value},
            "secret_env": {key: value},
            "files": {key: value},
            "secret_files": {key: value},
        }
    """
    service = store.get_service(service_id)
    if not service:
        raise ValueError("服务不存在")

    if not me.can_namespace(service["namespace_id"], "service.view"):
        raise PermissionError("无权查看服务")

    # 获取绑定的资源
    bindings = store.list_bindings(service_id)
    resources = []

    for binding in bindings:
        resource = store.get_resource(binding["resource_id"])
        if not resource:
            continue

        # 解密 secret 属性
        if include_secrets:
            type_def = get_resource_type(resource["type"])
            if type_def:
                decrypted_attributes = {}
                for attr_def in type_def.attributes:
                    attr_name = attr_def.name
                    if attr_name in resource["attributes"]:
                        value = resource["attributes"][attr_name]
                        if attr_def.type == "secret" and value:
                            decrypted_attributes[attr_name] = box.decrypt(value)
                        else:
                            decrypted_attributes[attr_name] = value
                resource["attributes"] = decrypted_attributes

        resources.append(resource)

    return export_resources(resources, include_secrets)
