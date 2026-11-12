"""环境预置的业务逻辑层（权限检查、CRUD、应用预置）。"""
from __future__ import annotations

from typing import Any, Dict, List

from ..access import Principal
from ..errors import Forbidden, NotFound
from ..security import SecretBox
from ..store import Store
from ..validation import reject_unknown, text_field
from .access import namespace_or_404


def can_manage_presets(me: Principal) -> bool:
    """检查是否有管理预置的权限"""
    return me.can("config.manage")


def can_view_presets(me: Principal) -> bool:
    """检查是否有查看预置的权限"""
    return me.can_anywhere("config.manage") or me.can_anywhere("environment.config")


def list_presets(store: Store, me: Principal) -> List[Dict[str, Any]]:
    """列出所有环境预置"""
    if not can_view_presets(me):
        raise Forbidden("没有查看预置的权限")

    return store.list_presets()


def get_preset(store: Store, me: Principal, preset_id: int) -> Dict[str, Any]:
    """获取环境预置详情"""
    if not can_view_presets(me):
        raise Forbidden("没有查看预置的权限")

    preset = store.get_preset(preset_id)
    if not preset:
        raise NotFound("预置不存在")

    return preset


def create_preset(store: Store, me: Principal, data: Any) -> Dict[str, Any]:
    """创建环境预置"""
    if not can_manage_presets(me):
        raise Forbidden("没有管理预置的权限")

    if not isinstance(data, dict):
        raise ValueError("请求体必须是对象")

    reject_unknown(data, ("name", "description", "profile_template", "default_config_sets",
                         "required_resource_types", "required_params"), "包含不支持的字段")

    name = text_field(data, "name", 80)
    if not name:
        raise ValueError("请填写预置名称")

    description = text_field(data, "description", 300)

    profile_template = data.get("profile_template", {})
    if not isinstance(profile_template, dict):
        raise ValueError("profile_template 必须是对象")

    default_config_sets = data.get("default_config_sets", [])
    if not isinstance(default_config_sets, list):
        raise ValueError("default_config_sets 必须是数组")

    required_resource_types = data.get("required_resource_types", [])
    if not isinstance(required_resource_types, list):
        raise ValueError("required_resource_types 必须是数组")

    required_params = data.get("required_params", [])
    if not isinstance(required_params, list):
        raise ValueError("required_params 必须是数组")

    preset_id = store.create_preset(name, description, profile_template, default_config_sets,
                                   required_resource_types, required_params, me.id)

    return store.get_preset(preset_id)  # type: ignore[return-value]


def update_preset(store: Store, me: Principal, preset_id: int, data: Any) -> Dict[str, Any]:
    """更新环境预置"""
    if not can_manage_presets(me):
        raise Forbidden("没有管理预置的权限")

    preset = store.get_preset(preset_id)
    if not preset:
        raise NotFound("预置不存在")

    if not isinstance(data, dict):
        raise ValueError("请求体必须是对象")

    reject_unknown(data, ("name", "description", "profile_template", "default_config_sets",
                         "required_resource_types", "required_params"), "包含不支持的字段")

    name = text_field(data, "name", 80)
    if not name:
        raise ValueError("请填写预置名称")

    description = text_field(data, "description", 300)

    profile_template = data.get("profile_template", {})
    if not isinstance(profile_template, dict):
        raise ValueError("profile_template 必须是对象")

    default_config_sets = data.get("default_config_sets", [])
    if not isinstance(default_config_sets, list):
        raise ValueError("default_config_sets 必须是数组")

    required_resource_types = data.get("required_resource_types", [])
    if not isinstance(required_resource_types, list):
        raise ValueError("required_resource_types 必须是数组")

    required_params = data.get("required_params", [])
    if not isinstance(required_params, list):
        raise ValueError("required_params 必须是数组")

    if not store.update_preset(preset_id, name, description, profile_template, default_config_sets,
                              required_resource_types, required_params, me.id):
        raise NotFound("预置不存在")

    return store.get_preset(preset_id)  # type: ignore[return-value]


def delete_preset(store: Store, me: Principal, preset_id: int) -> None:
    """删除环境预置"""
    if not can_manage_presets(me):
        raise Forbidden("没有管理预置的权限")

    if not store.delete_preset(preset_id):
        raise NotFound("预置不存在")


def apply_preset(store: Store, box: SecretBox, me: Principal, namespace_id: int, preset_id: int) -> Dict[str, Any]:
    """应用预置到环境"""
    namespace = namespace_or_404(store, me, namespace_id)

    if not me.can_namespace(namespace_id, "environment.config"):
        raise Forbidden("没有修改该环境的权限")

    preset = store.get_preset(preset_id)
    if not preset:
        raise NotFound("预置不存在")

    # 应用预置
    store.apply_preset_to_environment(namespace_id, preset, me.id)

    # 返回更新后的环境信息
    from .environments import get_environment

    return get_environment(store, me, namespace_id)
