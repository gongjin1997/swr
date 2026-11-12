"""配置管理服务层：业务逻辑和权限控制"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..access import Principal
from ..errors import Forbidden, NotFound
from ..store import Store
from ..store.enhanced_config import (
    ConfigKind,
    ConfigProcessType,
    ConfigStage,
    apply_config_template,
    batch_create_configs,
    create_config_entry,
    create_config_snapshot,
    delete_config_entry,
    get_config_entry,
    get_config_snapshot,
    list_config_entries,
    list_config_history,
    resolve_config,
    update_config_entry,
)


def create_config(
    store: Store,
    me: Principal,
    scope_type: str,
    scope_id: int,
    key: str,
    value: str,
    *,
    stage: str = ConfigStage.APP,
    kind: str = ConfigKind.COMMON,
    process_type: str = ConfigProcessType.ENV,
    locked: bool = False,
    description: str = "",
) -> Dict[str, Any]:
    """创建配置"""
    # 权限检查
    _check_config_permission(store, me, scope_type, scope_id, "config.manage")

    return create_config_entry(
        store,
        scope_type=scope_type,
        scope_id=scope_id,
        key=key,
        value=value,
        stage=stage,
        kind=kind,
        process_type=process_type,
        locked=locked,
        description=description,
        created_by=me.user_id,
    )


def update_config(
    store: Store,
    me: Principal,
    entry_id: int,
    value: str,
    *,
    kind: Optional[str] = None,
    process_type: Optional[str] = None,
    locked: Optional[bool] = None,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """更新配置"""
    # 获取配置条目
    entry = get_config_entry(store, entry_id)
    if not entry:
        raise NotFound(f"配置条目 {entry_id} 不存在")

    # 权限检查
    _check_config_permission(store, me, entry["scope_type"], entry["scope_id"], "config.manage")

    # 检查是否被更高优先级锁定
    if _is_locked_by_higher_priority(store, entry):
        raise Forbidden("此配置已被更高优先级锁定，无法修改")

    return update_config_entry(
        store,
        entry_id=entry_id,
        value=value,
        kind=kind,
        process_type=process_type,
        locked=locked,
        description=description,
        changed_by=me.user_id,
    )


def delete_config(
    store: Store,
    me: Principal,
    entry_id: int,
) -> None:
    """删除配置"""
    # 获取配置条目
    entry = get_config_entry(store, entry_id)
    if not entry:
        return

    # 权限检查
    _check_config_permission(store, me, entry["scope_type"], entry["scope_id"], "config.manage")

    delete_config_entry(store, entry_id, deleted_by=me.user_id)


def list_configs(
    store: Store,
    me: Principal,
    *,
    scope_type: Optional[str] = None,
    scope_id: Optional[int] = None,
    stage: Optional[str] = None,
    kind: Optional[str] = None,
    process_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """列出配置"""
    # 权限检查
    if scope_type and scope_id:
        _check_config_permission(store, me, scope_type, scope_id, "config.view")

    return list_config_entries(
        store,
        scope_type=scope_type,
        scope_id=scope_id,
        stage=stage,
        kind=kind,
        process_type=process_type,
    )


def get_service_config(
    store: Store,
    me: Principal,
    service_id: int,
    stage: str = ConfigStage.APP,
) -> Dict[str, Any]:
    """获取服务的完整配置（按优先级解析）"""
    # 获取服务
    service = store.execute_one("SELECT * FROM services WHERE id = %s", service_id)
    if not service:
        raise NotFound(f"服务 {service_id} 不存在")

    # 权限检查
    if not me.can_namespace(service["namespace_id"], "config.view"):
        raise Forbidden("无权查看此服务配置")

    return resolve_config(store, service_id, stage)


def apply_template(
    store: Store,
    me: Principal,
    template_id: int,
    scope_type: str,
    scope_id: int,
) -> List[Dict[str, Any]]:
    """应用配置模板"""
    # 权限检查
    _check_config_permission(store, me, scope_type, scope_id, "config.manage")

    return apply_config_template(
        store,
        template_id=template_id,
        scope_type=scope_type,
        scope_id=scope_id,
        created_by=me.user_id,
    )


def batch_create(
    store: Store,
    me: Principal,
    scope_type: str,
    scope_id: int,
    configs: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """批量创建配置"""
    # 权限检查
    _check_config_permission(store, me, scope_type, scope_id, "config.manage")

    return batch_create_configs(
        store,
        scope_type=scope_type,
        scope_id=scope_id,
        configs=configs,
        created_by=me.user_id,
    )


def get_config_history(
    store: Store,
    me: Principal,
    *,
    config_entry_id: Optional[int] = None,
    scope_type: Optional[str] = None,
    scope_id: Optional[int] = None,
    key: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """获取配置变更历史"""
    # 权限检查
    if scope_type and scope_id:
        _check_config_permission(store, me, scope_type, scope_id, "config.view")

    return list_config_history(
        store,
        config_entry_id=config_entry_id,
        scope_type=scope_type,
        scope_id=scope_id,
        key=key,
        limit=limit,
        offset=offset,
    )


def snapshot_config(
    store: Store,
    deployment_id: int,
    service_id: int,
    stage: str = ConfigStage.APP,
) -> int:
    """创建配置快照（部署时调用）"""
    # 解析配置
    config = resolve_config(store, service_id, stage)

    # 创建快照
    return create_config_snapshot(
        store,
        deployment_id=deployment_id,
        service_id=service_id,
        config=config,
        stage=stage,
    )


def get_deployment_config_snapshot(
    store: Store,
    me: Principal,
    deployment_id: int,
) -> Optional[Dict[str, Any]]:
    """获取部署的配置快照"""
    # 获取部署记录
    deployment = store.execute_one(
        "SELECT * FROM deployments WHERE id = %s",
        deployment_id
    )
    if not deployment:
        raise NotFound(f"部署记录 {deployment_id} 不存在")

    # 权限检查
    if not me.can_namespace(deployment["namespace_id"], "deploy.view"):
        raise Forbidden("无权查看此部署")

    return get_config_snapshot(store, deployment_id)


def compare_configs(
    store: Store,
    me: Principal,
    service_id: int,
    deployment_id_1: int,
    deployment_id_2: int,
) -> Dict[str, Any]:
    """对比两个部署的配置差异"""
    # 权限检查
    service = store.execute_one("SELECT * FROM services WHERE id = %s", service_id)
    if not service:
        raise NotFound(f"服务 {service_id} 不存在")

    if not me.can_namespace(service["namespace_id"], "config.view"):
        raise Forbidden("无权查看此服务配置")

    # 获取两个快照
    snapshot1 = get_config_snapshot(store, deployment_id_1)
    snapshot2 = get_config_snapshot(store, deployment_id_2)

    if not snapshot1 or not snapshot2:
        raise NotFound("配置快照不存在")

    config1 = snapshot1["snapshot_data"]
    config2 = snapshot2["snapshot_data"]

    # 对比差异
    added = {}
    removed = {}
    changed = {}

    # 找出新增和修改
    for key, value2 in config2.items():
        if key not in config1:
            added[key] = value2
        elif config1[key]["value"] != value2["value"]:
            changed[key] = {
                "old": config1[key],
                "new": value2,
            }

    # 找出删除
    for key, value1 in config1.items():
        if key not in config2:
            removed[key] = value1

    return {
        "deployment_1": {
            "id": deployment_id_1,
            "config_hash": snapshot1["config_hash"],
            "created_at": snapshot1["created_at"],
        },
        "deployment_2": {
            "id": deployment_id_2,
            "config_hash": snapshot2["config_hash"],
            "created_at": snapshot2["created_at"],
        },
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def export_configs(
    store: Store,
    me: Principal,
    scope_type: str,
    scope_id: int,
    *,
    format: str = "json",
) -> str:
    """导出配置（JSON/YAML）"""
    # 权限检查
    _check_config_permission(store, me, scope_type, scope_id, "config.view")

    # 获取配置
    configs = list_config_entries(
        store,
        scope_type=scope_type,
        scope_id=scope_id,
    )

    # 转换为导出格式
    export_data = []
    for config in configs:
        export_data.append({
            "key": config["key"],
            "value": config["value"],
            "stage": config["stage"],
            "kind": config["kind"],
            "process_type": config["process_type"],
            "locked": config["locked"],
            "description": config["description"],
        })

    if format == "yaml":
        import yaml
        return yaml.dump(export_data, allow_unicode=True)
    else:
        import json
        return json.dumps(export_data, ensure_ascii=False, indent=2)


def import_configs(
    store: Store,
    me: Principal,
    scope_type: str,
    scope_id: int,
    data: str,
    *,
    format: str = "json",
    overwrite: bool = False,
) -> List[Dict[str, Any]]:
    """导入配置（JSON/YAML）"""
    # 权限检查
    _check_config_permission(store, me, scope_type, scope_id, "config.manage")

    # 解析数据
    if format == "yaml":
        import yaml
        configs = yaml.safe_load(data)
    else:
        import json
        configs = json.loads(data)

    # 批量创建
    return batch_create_configs(
        store,
        scope_type=scope_type,
        scope_id=scope_id,
        configs=configs,
        created_by=me.user_id,
    )


# === 内部辅助函数 ===

def _check_config_permission(
    store: Store,
    me: Principal,
    scope_type: str,
    scope_id: int,
    permission: str,
) -> None:
    """检查配置权限"""
    if scope_type == "platform":
        if not me.is_platform_admin:
            raise Forbidden("需要平台管理员权限")
    elif scope_type == "project":
        if not me.can_project(scope_id, permission):
            raise Forbidden(f"无权操作项目配置")
    elif scope_type == "environment":
        if not me.can_namespace(scope_id, permission):
            raise Forbidden(f"无权操作环境配置")
    elif scope_type == "service":
        service = store.execute_one("SELECT * FROM services WHERE id = %s", scope_id)
        if not service:
            raise NotFound(f"服务 {scope_id} 不存在")
        if not me.can_namespace(service["namespace_id"], permission):
            raise Forbidden(f"无权操作服务配置")


def _is_locked_by_higher_priority(
    store: Store,
    entry: Dict[str, Any],
) -> bool:
    """检查配置是否被更高优先级锁定"""
    # 查询所有更高优先级的同名配置
    higher_configs = store.execute_all(
        """SELECT * FROM config_entries
           WHERE key = %s
           AND stage = %s
           AND priority > %s
           AND locked = TRUE""",
        entry["key"],
        entry["stage"],
        entry["priority"]
    )

    return len(higher_configs) > 0
