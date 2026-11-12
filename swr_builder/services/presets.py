"""环境预置的业务逻辑和应用。

预置包含：
- profile_template: 环境档案模板
- config_defaults: 默认配置集列表 [{scope_type, name, kind, entries}]
- required_resources: 必须绑定的资源类型列表
- required_params: 必填参数 key 列表

纳管命名空间时可选择一个预置，平台将其复制为该环境的档案和配置集。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..access.principal import Principal
from ..crypto import SecretBox
from ..store import Store


def can_manage_presets(me: Principal) -> bool:
    """检查是否有权管理预置"""
    return me.has("environment.config")


def require_manage_presets(me: Principal) -> None:
    """要求有管理预置的权限"""
    if not can_manage_presets(me):
        raise PermissionError("无权管理环境预置")


def list_presets(store: Store, me: Principal, limit: int = 100, offset: int = 0) -> Dict[str, Any]:
    """列出预置"""
    if not can_manage_presets(me):
        return {"items": [], "total": 0}
    items, total = store.list_presets(limit, offset)
    return {"items": items, "total": total}


def get_preset(store: Store, me: Principal, preset_id: int) -> Dict[str, Any]:
    """获取预置详情"""
    require_manage_presets(me)
    preset = store.get_preset(preset_id)
    if not preset:
        raise ValueError("预置不存在")
    return preset


def create_preset(store: Store, me: Principal, data: Any) -> Dict[str, Any]:
    """创建预置"""
    require_manage_presets(me)

    name = data.get("name", "").strip()
    if not name:
        raise ValueError("预置名称不能为空")

    profile_template = data.get("profile_template", {})
    config_defaults = data.get("config_defaults", [])
    required_resources = data.get("required_resources", [])
    required_params = data.get("required_params", [])
    description = data.get("description", "")

    return store.create_preset(name, profile_template, config_defaults,
                               required_resources=required_resources,
                               required_params=required_params,
                               description=description,
                               created_by=me.user_id)


def update_preset(store: Store, me: Principal, preset_id: int, data: Any) -> Dict[str, Any]:
    """更新预置"""
    require_manage_presets(me)

    preset = store.get_preset(preset_id)
    if not preset:
        raise ValueError("预置不存在")

    return store.update_preset(preset_id,
                               name=data.get("name"),
                               description=data.get("description"),
                               profile_template=data.get("profile_template"),
                               config_defaults=data.get("config_defaults"),
                               required_resources=data.get("required_resources"),
                               required_params=data.get("required_params"))


def delete_preset(store: Store, me: Principal, preset_id: int) -> None:
    """删除预置"""
    require_manage_presets(me)

    preset = store.get_preset(preset_id)
    if not preset:
        raise ValueError("预置不存在")

    store.delete_preset(preset_id)


def apply_preset(store: Store, box: SecretBox, me: Principal, namespace_id: int, preset_id: int) -> Dict[str, Any]:
    """
    将预置应用到环境：复制档案模板和默认配置集。

    返回创建的配置集列表。
    """
    from ..services.environments import normalize_profile, save_profile
    from ..services.configsets import create_config_set

    require_manage_presets(me)

    preset = store.get_preset(preset_id)
    if not preset:
        raise ValueError("预置不存在")

    namespace = store.get_namespace(namespace_id)
    if not namespace:
        raise ValueError("命名空间不存在")

    # 1. 应用环境档案模板
    profile_template = preset["profile_template"]
    normalized_profile = normalize_profile(profile_template)
    save_profile(store, me, namespace_id, normalized_profile, note=f"从预置 {preset['name']} 复制")

    # 2. 创建默认配置集
    created_sets = []
    for config_default in preset["config_defaults"]:
        scope_type = config_default.get("scope_type", "environment")
        scope_id = namespace_id if scope_type == "environment" else config_default.get("scope_id", 0)
        name = config_default["name"]
        kind = config_default["kind"]
        entries = config_default.get("entries", [])
        description = config_default.get("description", f"从预置 {preset['name']} 复制")

        # 检查是否已存在
        existing = store.list_config_sets([(scope_type, scope_id)])
        if any(s["name"] == name and s["kind"] == kind for s in existing):
            continue  # 跳过已存在的配置集

        config_set = create_config_set(store, box, me, {
            "scope_type": scope_type,
            "scope_id": scope_id,
            "name": name,
            "kind": kind,
            "description": description,
            "entries": entries,
        })
        created_sets.append(config_set)

    return {"profile_applied": True, "config_sets_created": len(created_sets), "config_sets": created_sets}


def check_required_params(store: Store, service: Dict[str, Any], preset_id: Optional[int] = None) -> List[str]:
    """
    检查服务是否满足必填参数要求。

    返回缺失的参数 key 列表。
    """
    if not preset_id:
        # 没有预置，不检查
        return []

    preset = store.get_preset(preset_id)
    if not preset:
        return []

    required_params = preset.get("required_params", [])
    if not required_params:
        return []

    # 收集服务的所有配置 key
    from ..delivery.resolve import layers
    sets = store.list_config_sets(layers(service))

    all_keys = set()
    for config_set in sets:
        if config_set.get("version"):
            version = store.get_config_version(config_set["id"], config_set["version"])
            if version:
                for entry in version["entries"]:
                    all_keys.add(entry["key"])

    # 检查缺失
    missing = [key for key in required_params if key not in all_keys]
    return missing
