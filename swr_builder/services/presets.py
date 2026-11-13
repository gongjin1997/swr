"""环境预置的业务逻辑（权限检查、CRUD、套用）。

预置只保留档案模板与默认配置集，环境要求（required_params/required_resources）已并入档案。
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..access import Principal
from ..errors import Forbidden, NotFound
from ..store import Store
from ..validation import reject_unknown, text_field
from .access import namespace_or_404


def can_manage_presets(me: Principal) -> bool:
    return me.can("config.manage")


def can_view_presets(me: Principal) -> bool:
    return me.can_anywhere("config.manage") or me.can_anywhere("environment.config")


def list_presets(store: Store, me: Principal) -> List[Dict[str, Any]]:
    if not can_view_presets(me):
        raise Forbidden("没有查看预置的权限")
    return store.list_presets()


def get_preset(store: Store, me: Principal, preset_id: int) -> Dict[str, Any]:
    if not can_view_presets(me):
        raise Forbidden("没有查看预置的权限")
    preset = store.get_preset(preset_id)
    if not preset:
        raise NotFound("预置不存在")
    return preset


def create_preset(store: Store, me: Principal, data: Any) -> Dict[str, Any]:
    if not can_manage_presets(me):
        raise Forbidden("没有管理预置的权限")
    if not isinstance(data, dict):
        raise ValueError("请求体必须是对象")
    reject_unknown(data, ("name", "description", "profile_template", "default_config_sets"), "包含不支持的字段")
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
    for config_set in default_config_sets:
        if not isinstance(config_set, dict):
            raise ValueError("配置集必须是对象")
        if config_set.get("kind") not in ("env", "files"):
            raise ValueError("默认配置集只能是 env 或 files（不能包含敏感值）")
    preset_id = store.create_preset(name, description, profile_template, default_config_sets, me.id)
    return store.get_preset(preset_id)  # type: ignore[return-value]


def update_preset(store: Store, me: Principal, preset_id: int, data: Any) -> Dict[str, Any]:
    if not can_manage_presets(me):
        raise Forbidden("没有管理预置的权限")
    preset = store.get_preset(preset_id)
    if not preset:
        raise NotFound("预置不存在")
    if not isinstance(data, dict):
        raise ValueError("请求体必须是对象")
    reject_unknown(data, ("base_version", "name", "description", "profile_template", "default_config_sets"),
                   "包含不支持的字段")
    base_version = data.get("base_version")
    if not isinstance(base_version, int) or isinstance(base_version, bool):
        raise ValueError("base_version 必须是整数")
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
    for config_set in default_config_sets:
        if not isinstance(config_set, dict):
            raise ValueError("配置集必须是对象")
        if config_set.get("kind") not in ("env", "files"):
            raise ValueError("默认配置集只能是 env 或 files（不能包含敏感值）")
    store.update_preset(preset_id, base_version, name, description, profile_template, default_config_sets, me.id)
    return store.get_preset(preset_id)  # type: ignore[return-value]


def delete_preset(store: Store, me: Principal, preset_id: int) -> None:
    if not can_manage_presets(me):
        raise Forbidden("没有管理预置的权限")
    if not store.delete_preset(preset_id):
        raise NotFound("预置不存在")


def apply_preset(store: Store, me: Principal, namespace_id: int, preset_id: int) -> Dict[str, Any]:
    """套用预置到环境：档案覆盖（带 base_version），同名配置集已存在时跳过。"""
    namespace_or_404(store, me, namespace_id)
    if not me.can_namespace(namespace_id, "environment.config"):
        raise Forbidden("没有修改该环境的权限")
    preset = store.get_preset(preset_id)
    if not preset:
        raise NotFound("预置不存在")
    store.apply_preset_to_environment(namespace_id, preset, me.id)
    from .environments import get_environment
    return get_environment(store, me, namespace_id)
