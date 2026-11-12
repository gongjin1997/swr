"""资源管理的业务逻辑和权限检查。

权限规则：
- resource.view: 查看全局和命名空间资源
- resource.manage: 创建、修改、删除资源
- service.config: 改变服务的资源绑定
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ..access.principal import Principal
from ..crypto import SecretBox
from ..delivery import resources as resource_types
from ..store import Store


def can_view_resource(me: Principal, resource: Dict[str, Any]) -> bool:
    """检查是否有权查看资源"""
    scope_type = resource["scope_type"]
    scope_id = resource["scope_id"]
    if scope_type == "platform":
        return me.has("resource.view")
    elif scope_type == "environment":
        # environment 实际上是 namespace
        return me.has("resource.view", namespace=scope_id)
    return False


def can_manage_resource(me: Principal, scope_type: str, scope_id: int) -> bool:
    """检查是否有权在该作用域下管理资源"""
    if scope_type == "platform":
        return me.has("resource.manage")
    elif scope_type == "environment":
        return me.has("resource.manage", namespace=scope_id)
    return False


def require_view_resource(me: Principal, resource: Dict[str, Any]) -> None:
    """要求有查看资源的权限"""
    if not can_view_resource(me, resource):
        raise PermissionError("无权查看该资源")


def require_manage_resource(me: Principal, scope_type: str, scope_id: int) -> None:
    """要求有管理资源的权限"""
    if not can_manage_resource(me, scope_type, scope_id):
        raise PermissionError("无权在该作用域管理资源")


def list_resources(store: Store, me: Principal, *, scope_type: Optional[str] = None,
                   scope_id: Optional[int] = None, type_name: Optional[str] = None,
                   limit: int = 100, offset: int = 0) -> Dict[str, Any]:
    """列出资源，过滤权限"""
    items, total = store.list_resources(scope_type, scope_id, type_name, limit, offset)
    # 过滤掉没有权限查看的资源
    visible = []
    for item in items:
        if can_view_resource(me, item):
            # 屏蔽 secret 属性
            resource = store.get_resource(item["id"])
            if resource:
                resource["attributes"] = resource_types.mask_secrets(resource["type"], resource["attributes"])
                visible.append(resource)
    return {"items": visible, "total": len(visible)}


def get_resource(store: Store, me: Principal, resource_id: int, *, reveal: bool = False) -> Dict[str, Any]:
    """获取资源详情"""
    resource = store.get_resource(resource_id)
    if not resource:
        raise ValueError("资源不存在")
    require_view_resource(me, resource)

    # 屏蔽 secret 属性，除非有 secret.view 权限且 reveal=True
    if reveal and me.has("secret.view"):
        pass  # 不屏蔽
    else:
        resource["attributes"] = resource_types.mask_secrets(resource["type"], resource["attributes"])

    return resource


def create_resource(store: Store, box: SecretBox, me: Principal, data: Any) -> Dict[str, Any]:
    """创建资源"""
    scope_type = data.get("scope_type", "platform")
    scope_id = data.get("scope_id", 0)
    require_manage_resource(me, scope_type, scope_id)

    type_name = data.get("type")
    if not type_name:
        raise ValueError("缺少资源类型")

    resource_types.validate_type(type_name)

    name = data.get("name", "").strip()
    if not name:
        raise ValueError("资源名称不能为空")

    parent_id = data.get("parent_id")
    if parent_id is not None:
        parent = store.get_resource(parent_id)
        if not parent:
            raise ValueError("父资源不存在")
        # 检查父类型是否匹配
        expected_parent = resource_types.RESOURCE_TYPES[type_name].get("parent_type")
        if expected_parent and parent["type"] != expected_parent:
            raise ValueError(f"资源类型 {type_name} 的父类型必须是 {expected_parent}，而不是 {parent['type']}")

    attributes = data.get("attributes", {})
    normalized_attrs = resource_types.validate_attributes(type_name, attributes)

    # 加密 secret 属性
    secret_keys = resource_types.secret_attributes(type_name)
    encrypted_attrs = {}
    for key, value in normalized_attrs.items():
        if key in secret_keys:
            encrypted_attrs[key] = box.encrypt(str(value).encode("utf-8")).decode("ascii")
        else:
            encrypted_attrs[key] = value

    description = data.get("description", "")

    resource = store.create_resource(scope_type, scope_id, type_name, name, parent_id, encrypted_attrs,
                                     created_by=me.user_id, description=description)

    # 返回时屏蔽 secret
    resource["attributes"] = resource_types.mask_secrets(type_name, normalized_attrs)
    return resource


def update_resource(store: Store, box: SecretBox, me: Principal, resource_id: int, data: Any) -> Dict[str, Any]:
    """更新资源属性"""
    resource = store.get_resource(resource_id)
    if not resource:
        raise ValueError("资源不存在")
    require_manage_resource(me, resource["scope_type"], resource["scope_id"])

    type_name = resource["type"]
    attributes = data.get("attributes", {})

    # 合并现有属性：新属性覆盖，未提供的保持原值
    current_attrs = _decrypt_attributes(box, type_name, resource["attributes"])
    updated_attrs = {**current_attrs, **attributes}

    normalized_attrs = resource_types.validate_attributes(type_name, updated_attrs)

    # 加密 secret 属性
    secret_keys = resource_types.secret_attributes(type_name)
    encrypted_attrs = {}
    for key, value in normalized_attrs.items():
        if key in secret_keys:
            encrypted_attrs[key] = box.encrypt(str(value).encode("utf-8")).decode("ascii")
        else:
            encrypted_attrs[key] = value

    note = data.get("note", "")
    description = data.get("description")

    resource = store.update_resource(resource_id, encrypted_attrs, created_by=me.user_id, note=note,
                                     description=description)

    # 返回时屏蔽 secret
    resource["attributes"] = resource_types.mask_secrets(type_name, normalized_attrs)
    return resource


def delete_resource(store: Store, me: Principal, resource_id: int) -> None:
    """删除资源"""
    resource = store.get_resource(resource_id)
    if not resource:
        raise ValueError("资源不存在")
    require_manage_resource(me, resource["scope_type"], resource["scope_id"])

    # 检查是否有服务绑定
    usage = store.resource_usage(resource_id)
    if usage:
        service_names = ", ".join([u["service_name"] for u in usage[:3]])
        raise ValueError(f"资源被 {len(usage)} 个服务使用（{service_names}），不能删除")

    store.delete_resource(resource_id)


def list_resource_versions(store: Store, me: Principal, resource_id: int) -> List[Dict[str, Any]]:
    """列出资源的所有版本"""
    resource = store.get_resource(resource_id)
    if not resource:
        raise ValueError("资源不存在")
    require_view_resource(me, resource)

    return store.list_resource_versions(resource_id)


def get_resource_version(store: Store, box: SecretBox, me: Principal, resource_id: int, version_number: int, *,
                         reveal: bool = False) -> Dict[str, Any]:
    """获取资源的指定版本"""
    resource = store.get_resource(resource_id)
    if not resource:
        raise ValueError("资源不存在")
    require_view_resource(me, resource)

    version = store.get_resource_version(resource_id, version_number)
    if not version:
        raise ValueError("版本不存在")

    # 屏蔽 secret 属性
    if reveal and me.has("secret.view"):
        version["attributes"] = _decrypt_attributes(box, version["type"], version["attributes"])
    else:
        decrypted = _decrypt_attributes(box, version["type"], version["attributes"])
        version["attributes"] = resource_types.mask_secrets(version["type"], decrypted)

    return version


def resource_usage(store: Store, me: Principal, resource_id: int) -> List[Dict[str, Any]]:
    """查询资源被哪些服务使用"""
    resource = store.get_resource(resource_id)
    if not resource:
        raise ValueError("资源不存在")
    require_view_resource(me, resource)

    return store.resource_usage(resource_id)


def _decrypt_attributes(box: SecretBox, type_name: str, encrypted_attrs: Dict[str, Any]) -> Dict[str, Any]:
    """解密属性中的 secret 字段"""
    secret_keys = resource_types.secret_attributes(type_name)
    decrypted = {}
    for key, value in encrypted_attrs.items():
        if key in secret_keys and isinstance(value, str):
            try:
                decrypted[key] = box.decrypt(value.encode("ascii")).decode("utf-8")
            except Exception:
                decrypted[key] = value  # 解密失败，保持原值
        else:
            decrypted[key] = value
    return decrypted


def resolve_bindings(store: Store, box: SecretBox, service_id: int) -> tuple[List[Dict[str, Any]], Dict[int, int]]:
    """
    解析服务的资源绑定，返回 (带属性的资源链列表, {resource_id: version_id} 版本钉住映射)

    每个资源链包含 [自己, 父, 祖父, ...]，属性已解密。
    """
    bindings = store.list_bindings(service_id)
    chains = []
    version_pins = {}

    # 构建资源 ID -> 资源映射
    resource_ids = set()
    for binding in bindings:
        resource_ids.add(binding["resource_id"])
        # 收集父链
        parent_id = binding["parent_id"]
        while parent_id:
            resource_ids.add(parent_id)
            parent = store.get_resource(parent_id)
            if parent:
                parent_id = parent["parent_id"]
            else:
                break

    resources_by_id = {}
    for rid in resource_ids:
        res = store.get_resource(rid)
        if res:
            # 解密属性
            decrypted_attrs = _decrypt_attributes(box, res["type"], res["attributes"])
            resources_by_id[rid] = {
                "id": res["id"],
                "type": res["type"],
                "name": res["name"],
                "parent_id": res["parent_id"],
                "attributes": decrypted_attrs,
                "current_version_id": res["current_version_id"],
            }
            version_pins[rid] = res["current_version_id"]

    # 为每个绑定展开父链
    for binding in bindings:
        try:
            chain = resource_types.parent_chain(resources_by_id, binding["resource_id"])
            chains.append(chain)
        except ValueError:
            # 父链循环或资源缺失，跳过该绑定
            continue

    return chains, version_pins
