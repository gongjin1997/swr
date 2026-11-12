"""环境预置管理服务（基于 XAC 的应用预置概念）。

预置定义了环境的标准配置模板，包括：
- 环境档案模板
- 默认配置集
- 必需的资源类型
- 必填参数列表
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ..access.principal import Principal
from ..store import Store


def can_manage_presets(me: Principal) -> bool:
    """检查是否有权管理预置"""
    return me.has("preset.manage")


def list_presets(store: Store, me: Principal) -> Dict[str, Any]:
    """列出所有预置"""
    if not me.has("preset.view"):
        raise PermissionError("无权查看预置")

    items, total = store.list_presets()
    return {"items": items, "total": total}


def get_preset(store: Store, me: Principal, preset_id: int) -> Dict[str, Any]:
    """获取预置详情"""
    if not me.has("preset.view"):
        raise PermissionError("无权查看预置")

    preset = store.get_preset(preset_id)
    if not preset:
        raise ValueError("预置不存在")

    return preset


def create_preset(store: Store, me: Principal, data: Dict[str, Any]) -> Dict[str, Any]:
    """创建预置"""
    if not can_manage_presets(me):
        raise PermissionError("无权管理预置")

    name = data.get("name", "").strip()
    description = data.get("description", "")
    profile_template = data.get("profile_template", {})
    config_defaults = data.get("config_defaults", {})
    required_resources = data.get("required_resources", [])
    required_params = data.get("required_params", [])

    if not name:
        raise ValueError("预置名称不能为空")

    return store.create_preset(
        name=name,
        description=description,
        profile_template=profile_template,
        config_defaults=config_defaults,
        required_resources=required_resources,
        required_params=required_params,
        created_by=me.user_id,
    )


def update_preset(
    store: Store,
    me: Principal,
    preset_id: int,
    data: Dict[str, Any],
) -> Dict[str, Any]:
    """更新预置"""
    if not can_manage_presets(me):
        raise PermissionError("无权管理预置")

    preset = store.get_preset(preset_id)
    if not preset:
        raise ValueError("预置不存在")

    updates = {}
    if "name" in data:
        updates["name"] = data["name"].strip()
    if "description" in data:
        updates["description"] = data["description"]
    if "profile_template" in data:
        updates["profile_template"] = data["profile_template"]
    if "config_defaults" in data:
        updates["config_defaults"] = data["config_defaults"]
    if "required_resources" in data:
        updates["required_resources"] = data["required_resources"]
    if "required_params" in data:
        updates["required_params"] = data["required_params"]

    return store.update_preset(preset_id, updates)


def delete_preset(store: Store, me: Principal, preset_id: int) -> None:
    """删除预置"""
    if not can_manage_presets(me):
        raise PermissionError("无权管理预置")

    preset = store.get_preset(preset_id)
    if not preset:
        raise ValueError("预置不存在")

    store.delete_preset(preset_id)


def apply_preset_to_environment(
    store: Store,
    me: Principal,
    namespace_id: int,
    preset_id: int,
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    应用预置到环境。

    操作：
    1. 应用环境档案模板到环境
    2. 创建默认配置集（如果不存在）
    3. 返回需要创建的资源清单和必填参数列表

    Args:
        namespace_id: 目标环境 ID
        preset_id: 预置 ID
        overrides: 覆盖值（可选）

    Returns:
        {
            "profile_applied": bool,
            "config_sets_created": [name],
            "required_resources": [{type, description}],
            "required_params": [name],
        }
    """
    if not me.can_namespace(namespace_id, "namespace.configure"):
        raise PermissionError("无权配置环境")

    preset = store.get_preset(preset_id)
    if not preset:
        raise ValueError("预置不存在")

    overrides = overrides or {}
    result = {
        "profile_applied": False,
        "config_sets_created": [],
        "required_resources": preset["required_resources"],
        "required_params": preset["required_params"],
    }

    # 1. 应用环境档案模板
    profile_template = preset["profile_template"]
    if overrides.get("profile"):
        # 合并覆盖值
        profile_template = {**profile_template, **overrides["profile"]}

    # 检查环境档案是否已存在
    existing_profile = store.get_environment_profile(namespace_id)
    if not existing_profile or not existing_profile.get("profile"):
        # 创建环境档案
        store.upsert_environment_profile(
            namespace_id=namespace_id,
            profile=profile_template,
            operator_id=me.user_id,
        )
        result["profile_applied"] = True

    # 2. 应用默认配置集
    config_defaults = preset["config_defaults"]
    if overrides.get("config"):
        config_defaults = {**config_defaults, **overrides["config"]}

    for config_kind, entries in config_defaults.items():
        if not entries:
            continue

        # 检查配置集是否已存在
        existing_sets = store.list_config_sets([("environment", namespace_id)])
        existing_kinds = {s["kind"] for s in existing_sets if s["kind"] == config_kind}

        if config_kind not in existing_kinds:
            # 创建配置集
            config_set = store.create_config_set(
                scope_type="environment",
                scope_id=namespace_id,
                kind=config_kind,
                name=f"{config_kind}_default",
                operator_id=me.user_id,
            )

            # 创建第一个版本
            store.upsert_config_version(
                set_id=config_set["id"],
                entries=[{"key": k, "value": v} for k, v in entries.items()],
                operator_id=me.user_id,
                note=f"从预置 {preset['name']} 应用",
            )

            result["config_sets_created"].append(config_set["name"])

    return result


def validate_environment_preset_requirements(
    store: Store,
    me: Principal,
    namespace_id: int,
    preset_id: int,
) -> Dict[str, Any]:
    """
    校验环境是否满足预置的要求。

    Returns:
        {
            "satisfied": bool,
            "missing_resources": [{type, description}],
            "missing_params": [name],
        }
    """
    if not me.can_namespace(namespace_id, "namespace.view"):
        raise PermissionError("无权查看环境")

    preset = store.get_preset(preset_id)
    if not preset:
        raise ValueError("预置不存在")

    # 检查必需的资源
    required_resource_types = {r["type"] for r in preset["required_resources"]}
    existing_resources, _ = store.list_resources(
        scope_type="environment",
        scope_id=namespace_id,
        limit=1000,
        offset=0,
    )
    existing_types = {r["type"] for r in existing_resources}
    missing_resource_types = required_resource_types - existing_types
    missing_resources = [r for r in preset["required_resources"] if r["type"] in missing_resource_types]

    # 检查必填参数（简化：从配置集中查找）
    config_sets = store.list_config_sets([("environment", namespace_id)])
    all_params = set()
    for config_set in config_sets:
        if config_set.get("version"):
            version = store.get_config_version(config_set["id"], config_set["version"])
            if version:
                all_params.update(e["key"] for e in version["entries"])

    missing_params = [p for p in preset["required_params"] if p not in all_params]

    return {
        "satisfied": not missing_resources and not missing_params,
        "missing_resources": missing_resources,
        "missing_params": missing_params,
    }
