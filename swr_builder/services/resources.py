"""资源管理的业务逻辑层（权限检查、公开字段、CRUD）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..access import Principal
from ..delivery import resources as resource_types
from ..errors import Conflict, Forbidden, NotFound
from ..security import SecretBox
from ..store import Store
from ..validation import DNS_SUBDOMAIN_RE, reject_unknown, text_field


def can_view_resources(me: Principal, scope_type: str, scope_id: int) -> bool:
    """检查是否有查看资源的权限"""
    if scope_type == "platform":
        return me.can_anywhere("resource.view") or me.can_anywhere("resource.manage")
    elif scope_type == "environment":
        return me.can_namespace(scope_id, "resource.view") or me.can_namespace(scope_id, "resource.manage")
    return False


def can_manage_resources(me: Principal, scope_type: str, scope_id: int) -> bool:
    """检查是否有管理资源的权限"""
    if scope_type == "platform":
        return me.can("resource.manage")
    elif scope_type == "environment":
        return me.can_namespace(scope_id, "resource.manage")
    return False


def resource_or_404(store: Store, me: Principal, resource_id: int, action: str = "view") -> Dict[str, Any]:
    """获取资源并检查权限"""
    resource = store.get_resource(resource_id)
    if not resource:
        raise NotFound("资源不存在")

    if action == "view":
        if not can_view_resources(me, resource["scope_type"], resource["scope_id"]):
            raise NotFound("资源不存在")
    elif action in ("edit", "delete"):
        if not can_manage_resources(me, resource["scope_type"], resource["scope_id"]):
            raise Forbidden("没有管理该资源的权限")
    elif action == "reveal":
        # 查看敏感值需要 secret.view
        scope_type, scope_id = resource["scope_type"], resource["scope_id"]
        if scope_type == "platform":
            if not me.can("secret.view"):
                raise Forbidden("没有查看敏感配置的权限")
        else:
            if not me.can_namespace(scope_id, "secret.view"):
                raise Forbidden("没有查看敏感配置的权限")

    return resource


def public_resource(store: Store, me: Principal, resource: Dict[str, Any]) -> Dict[str, Any]:
    """返回公开字段（mask 敏感属性）"""
    masked_attrs = resource_types.mask_secrets(resource["type"], resource.get("attributes") or {})

    return {
        "id": resource["id"],
        "scope_type": resource["scope_type"],
        "scope_id": resource["scope_id"],
        "type": resource["type"],
        "name": resource["name"],
        "description": resource["description"],
        "version": resource.get("version") or 0,
        "attributes": masked_attrs,
        "created_at": resource["created_at"],
        "updated_at": resource["updated_at"],
        "can_edit": can_manage_resources(me, resource["scope_type"], resource["scope_id"]),
        "can_reveal": (me.can("secret.view") if resource["scope_type"] == "platform"
                      else me.can_namespace(resource["scope_id"], "secret.view")),
    }


def list_resources(store: Store, me: Principal, scope_type: Optional[str] = None,
                   scope_id: Optional[int] = None, type_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """列出资源（按权限筛选）"""
    all_resources = store.list_resources(scope_type, scope_id, type_name)

    # 按权限筛选
    visible = []
    for resource in all_resources:
        if can_view_resources(me, resource["scope_type"], resource["scope_id"]):
            visible.append(public_resource(store, me, resource))

    return visible


def get_resource(store: Store, me: Principal, resource_id: int) -> Dict[str, Any]:
    """获取资源详情"""
    resource = resource_or_404(store, me, resource_id, "view")
    result = public_resource(store, me, resource)

    # 增加使用情况
    result["binding_count"] = store.count_service_bindings(resource_id)

    return result


def create_resource(store: Store, box: SecretBox, me: Principal, data: Any) -> Dict[str, Any]:
    """创建资源"""
    if not isinstance(data, dict):
        raise ValueError("请求体必须是对象")

    reject_unknown(data, ("scope_type", "scope_id", "type", "name", "description", "attributes", "note"),
                   "包含不支持的字段")

    scope_type = data.get("scope_type")
    if scope_type not in ("platform", "environment"):
        raise ValueError("scope_type 必须是 platform 或 environment")

    scope_id = data.get("scope_id", 0)
    if scope_type == "platform":
        scope_id = 0
    elif not isinstance(scope_id, int) or scope_id <= 0:
        raise ValueError("environment scope 需要有效的 scope_id")

    # 权限检查
    if not can_manage_resources(me, scope_type, scope_id):
        raise Forbidden("没有在该层级创建资源的权限")

    # 校验作用域存在
    if scope_type == "environment":
        namespace = store.get_namespace(scope_id)
        if not namespace:
            raise NotFound("环境不存在")

    type_name = data.get("type")
    resource_types.validate_type(type_name)

    name = text_field(data, "name", 253)
    if not name or not DNS_SUBDOMAIN_RE.fullmatch(name):
        raise ValueError("资源名称必须是 DNS 子域名格式")

    description = text_field(data, "description", 300)

    attributes = data.get("attributes", {})
    if not isinstance(attributes, dict):
        raise ValueError("attributes 必须是对象")

    # 校验并加密属性
    validated_attrs = resource_types.validate_attributes(type_name, attributes, box)
    checksum = resource_types.checksum(validated_attrs)
    note = text_field(data, "note", 300)

    resource_id = store.create_resource(scope_type, scope_id, type_name, name, description,
                                       validated_attrs, checksum, note, me.id)

    return get_resource(store, me, resource_id)


def update_resource(store: Store, box: SecretBox, me: Principal, resource_id: int, data: Any) -> Dict[str, Any]:
    """更新资源（创建新版本）"""
    resource = resource_or_404(store, me, resource_id, "edit")

    if not isinstance(data, dict):
        raise ValueError("请求体必须是对象")

    reject_unknown(data, ("base_version", "attributes", "note", "description"), "包含不支持的字段")

    base_version = data.get("base_version")
    if not isinstance(base_version, int) or isinstance(base_version, bool):
        raise ValueError("base_version 必须是整数")

    attributes = data.get("attributes", {})
    if not isinstance(attributes, dict):
        raise ValueError("attributes 必须是对象")

    validated_attrs = resource_types.validate_attributes(resource["type"], attributes, box)
    checksum = resource_types.checksum(validated_attrs)
    note = text_field(data, "note", 300)
    description = text_field(data, "description", 300) if "description" in data else None

    store.add_resource_version(resource_id, base_version, validated_attrs, checksum, note, me.id, description)

    return get_resource(store, me, resource_id)


def delete_resource(store: Store, me: Principal, resource_id: int) -> None:
    """删除资源"""
    resource = resource_or_404(store, me, resource_id, "delete")

    # 检查是否有服务绑定
    binding_count = store.count_service_bindings(resource_id)
    if binding_count > 0:
        raise Conflict(f"资源被 {binding_count} 个服务使用，不能删除")

    store.delete_resource(resource_id)


def reveal_resource(store: Store, box: SecretBox, me: Principal, resource_id: int) -> Dict[str, Any]:
    """查看资源的敏感属性（解密）"""
    resource = resource_or_404(store, me, resource_id, "reveal")

    # 解密属性
    attributes = resource.get("attributes") or {}
    decrypted_attrs = {}

    for key, value in attributes.items():
        if key in resource_types.secret_attributes(resource["type"]):
            try:
                decrypted_attrs[key] = box.decrypt(value)
            except Exception:
                decrypted_attrs[key] = "[解密失败]"
        else:
            decrypted_attrs[key] = value

    return {
        "id": resource["id"],
        "type": resource["type"],
        "name": resource["name"],
        "version": resource.get("version") or 0,
        "attributes": decrypted_attrs,
    }


def list_resource_versions(store: Store, me: Principal, resource_id: int) -> List[Dict[str, Any]]:
    """列出资源版本历史"""
    resource_or_404(store, me, resource_id, "view")
    return store.list_resource_versions(resource_id)


def get_resource_version(store: Store, me: Principal, resource_id: int, version: int) -> Dict[str, Any]:
    """获取资源的指定版本"""
    resource_or_404(store, me, resource_id, "view")

    version_data = store.get_resource_version(resource_id, version)
    if not version_data:
        raise NotFound("资源版本不存在")

    resource = store.get_resource(resource_id)
    masked_attrs = resource_types.mask_secrets(resource["type"], version_data.get("attributes") or {})

    return {
        "resource_id": resource_id,
        "number": version_data["number"],
        "attributes": masked_attrs,
        "checksum": version_data["checksum"],
        "note": version_data.get("note", ""),
        "created_at": version_data["created_at"],
        "created_by": version_data.get("created_by"),
    }


def list_service_bindings(store: Store, me: Principal, service_id: int) -> List[Dict[str, Any]]:
    """列出服务的资源绑定"""
    from .access import service_or_404

    service_or_404(store, me, service_id)
    bindings = store.list_service_resource_bindings(service_id)

    result = []
    for binding in bindings:
        resource = binding["resource"]
        masked_attrs = resource_types.mask_secrets(resource["type"], resource.get("attributes") or {})

        result.append({
            "alias": binding["alias"],
            "resource_id": binding["resource_id"],
            "resource": {
                "id": resource["id"],
                "type": resource["type"],
                "name": resource["name"],
                "scope_type": resource["scope_type"],
                "scope_id": resource["scope_id"],
                "version": resource.get("version") or 0,
                "attributes": masked_attrs,
            }
        })

    return result


def bind_resource(store: Store, me: Principal, service_id: int, data: Any) -> Dict[str, Any]:
    """绑定资源到服务"""
    from .access import service_or_404

    service = service_or_404(store, me, service_id)

    if not me.can_namespace(service["namespace_id"], "service.config"):
        raise Forbidden("没有配置服务的权限")

    if not isinstance(data, dict):
        raise ValueError("请求体必须是对象")

    reject_unknown(data, ("resource_id", "alias"), "包含不支持的字段")

    resource_id = data.get("resource_id")
    if not isinstance(resource_id, int):
        raise ValueError("resource_id 必须是整数")

    alias = text_field(data, "alias", 63)
    if not alias or not DNS_SUBDOMAIN_RE.fullmatch(alias):
        raise ValueError("alias 必须是 DNS 子域名格式")

    # 检查资源是否存在且可访问
    resource = store.get_resource(resource_id)
    if not resource:
        raise NotFound("资源不存在")

    if not can_view_resources(me, resource["scope_type"], resource["scope_id"]):
        raise NotFound("资源不存在")

    # 创建绑定
    store.create_service_resource_binding(service_id, resource_id, alias)

    return {"alias": alias, "resource_id": resource_id}


def unbind_resource(store: Store, me: Principal, service_id: int, alias: str) -> None:
    """解绑资源"""
    from .access import service_or_404

    service = service_or_404(store, me, service_id)

    if not me.can_namespace(service["namespace_id"], "service.config"):
        raise Forbidden("没有配置服务的权限")

    if not store.delete_service_resource_binding(service_id, alias):
        raise NotFound("绑定不存在")


def preview_exports(store: Store, box: SecretBox, me: Principal, service_id: int, reveal: bool = False
                   ) -> Dict[str, Any]:
    """预览服务当前绑定的资源导出"""
    from .access import service_or_404

    service = service_or_404(store, me, service_id)
    bindings = store.list_service_resource_bindings(service_id)

    # 按 alias 组织导出
    exports_by_alias: Dict[str, Dict[str, Any]] = {}

    for binding in bindings:
        alias = binding["alias"]
        resource = binding["resource"]

        # 计算导出（需要解密属性）
        attributes = resource.get("attributes") or {}
        if reveal:
            # 解密
            decrypted = {}
            for key, value in attributes.items():
                if key in resource_types.secret_attributes(resource["type"]):
                    try:
                        decrypted[key] = box.decrypt(value)
                    except Exception:
                        decrypted[key] = ""
                else:
                    decrypted[key] = value
            attributes = decrypted

        # 构建资源链（简化版本：仅当前资源，不展开父链）
        chain = [{"type": resource["type"], "attributes": attributes}]

        try:
            exports = resource_types.compute_exports(chain)
            exports_by_alias[alias] = {
                "resource_id": binding["resource_id"],
                "resource_type": resource["type"],
                "resource_name": resource["name"],
                "env": exports.get("env", []),
                "secret_env": [(k, "******") for k, v in exports.get("secret_env", [])] if not reveal else exports.get("secret_env", []),
            }
        except ValueError:
            # 导出计算失败，跳过
            continue

    return {"exports": exports_by_alias}
