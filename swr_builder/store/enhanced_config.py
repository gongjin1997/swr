"""增强配置系统 Store 层：多维度配置模型"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from ..store import Store


class ConfigPriority:
    """配置优先级常量"""
    DEFAULT = 1
    SERVICE_COMMON = 2
    SERVICE_SECRET = 3
    ENVIRONMENT_COMMON = 4
    ENVIRONMENT_SECRET = 5
    PROJECT_COMMON = 4  # 与环境同级
    PROJECT_SECRET = 5
    PLATFORM_COMMON = 6
    PLATFORM_SECRET = 7


class ConfigStage:
    """配置阶段常量"""
    INIT = "init"  # 初始化阶段（资源申请、初始化脚本）
    APP = "app"    # 应用阶段（运行时配置）


class ConfigKind:
    """配置类型常量"""
    COMMON = "common"  # 普通配置
    SECRET = "secret"  # 敏感配置


class ConfigProcessType:
    """配置处理类型常量"""
    ENV = "env"         # 环境变量
    FILE = "file"       # 配置文件
    TEMPLATE = "template"  # 模板占位符


def get_priority_by_scope(scope_type: str, kind: str) -> int:
    """根据作用域和类型获取优先级"""
    if scope_type == "platform":
        return ConfigPriority.PLATFORM_SECRET if kind == "secret" else ConfigPriority.PLATFORM_COMMON
    elif scope_type == "project":
        return ConfigPriority.PROJECT_SECRET if kind == "secret" else ConfigPriority.PROJECT_COMMON
    elif scope_type == "environment":
        return ConfigPriority.ENVIRONMENT_SECRET if kind == "secret" else ConfigPriority.ENVIRONMENT_COMMON
    elif scope_type == "service":
        return ConfigPriority.SERVICE_SECRET if kind == "secret" else ConfigPriority.SERVICE_COMMON
    else:
        return ConfigPriority.DEFAULT


def create_config_entry(
    store: Store,
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
    created_by: Optional[int] = None,
) -> Dict[str, Any]:
    """创建配置条目"""
    priority = get_priority_by_scope(scope_type, kind)

    row = store.execute_one(
        """INSERT INTO config_entries
           (scope_type, scope_id, stage, priority, kind, process_type, key, value, locked, description, created_by, created_at, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
           ON CONFLICT (scope_type, scope_id, stage, key)
           DO UPDATE SET value = EXCLUDED.value, kind = EXCLUDED.kind, process_type = EXCLUDED.process_type,
                         locked = EXCLUDED.locked, description = EXCLUDED.description,
                         priority = EXCLUDED.priority, version = config_entries.version + 1,
                         updated_at = NOW()
           RETURNING *""",
        scope_type, scope_id, stage, priority, kind, process_type, key, value, locked, description, created_by
    )

    # 记录历史
    _record_config_history(
        store,
        config_entry_id=row["id"],
        scope_type=scope_type,
        scope_id=scope_id,
        stage=stage,
        priority=priority,
        kind=kind,
        process_type=process_type,
        key=key,
        old_value=None,
        new_value=value,
        action="create",
        changed_by=created_by,
    )

    return row


def update_config_entry(
    store: Store,
    entry_id: int,
    value: str,
    *,
    kind: Optional[str] = None,
    process_type: Optional[str] = None,
    locked: Optional[bool] = None,
    description: Optional[str] = None,
    changed_by: Optional[int] = None,
) -> Dict[str, Any]:
    """更新配置条目"""
    # 先查询旧值
    old_entry = store.execute_one(
        "SELECT * FROM config_entries WHERE id = %s",
        entry_id
    )

    if not old_entry:
        raise ValueError(f"配置条目 {entry_id} 不存在")

    # 构建更新语句
    updates = ["value = %s", "version = version + 1", "updated_at = NOW()"]
    params = [value]

    if kind is not None:
        updates.append("kind = %s")
        params.append(kind)
        # 更新优先级
        new_priority = get_priority_by_scope(old_entry["scope_type"], kind)
        updates.append("priority = %s")
        params.append(new_priority)

    if process_type is not None:
        updates.append("process_type = %s")
        params.append(process_type)

    if locked is not None:
        updates.append("locked = %s")
        params.append(locked)

    if description is not None:
        updates.append("description = %s")
        params.append(description)

    params.append(entry_id)

    row = store.execute_one(
        f"UPDATE config_entries SET {', '.join(updates)} WHERE id = %s RETURNING *",
        *params
    )

    # 记录历史
    _record_config_history(
        store,
        config_entry_id=entry_id,
        scope_type=old_entry["scope_type"],
        scope_id=old_entry["scope_id"],
        stage=old_entry["stage"],
        priority=row["priority"],
        kind=row["kind"],
        process_type=row["process_type"],
        key=old_entry["key"],
        old_value=old_entry["value"],
        new_value=value,
        action="update",
        changed_by=changed_by,
    )

    return row


def delete_config_entry(
    store: Store,
    entry_id: int,
    *,
    deleted_by: Optional[int] = None,
) -> None:
    """删除配置条目"""
    # 先查询配置信息
    entry = store.execute_one(
        "SELECT * FROM config_entries WHERE id = %s",
        entry_id
    )

    if not entry:
        return

    # 记录历史
    _record_config_history(
        store,
        config_entry_id=entry_id,
        scope_type=entry["scope_type"],
        scope_id=entry["scope_id"],
        stage=entry["stage"],
        priority=entry["priority"],
        kind=entry["kind"],
        process_type=entry["process_type"],
        key=entry["key"],
        old_value=entry["value"],
        new_value=None,
        action="delete",
        changed_by=deleted_by,
    )

    # 删除配置
    store.execute("DELETE FROM config_entries WHERE id = %s", entry_id)


def get_config_entry(store: Store, entry_id: int) -> Optional[Dict[str, Any]]:
    """获取配置条目"""
    return store.execute_one(
        "SELECT * FROM config_entries WHERE id = %s",
        entry_id
    )


def list_config_entries(
    store: Store,
    *,
    scope_type: Optional[str] = None,
    scope_id: Optional[int] = None,
    stage: Optional[str] = None,
    kind: Optional[str] = None,
    process_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """列出配置条目"""
    conditions = []
    params = []

    if scope_type is not None:
        conditions.append("scope_type = %s")
        params.append(scope_type)

    if scope_id is not None:
        conditions.append("scope_id = %s")
        params.append(scope_id)

    if stage is not None:
        conditions.append("stage = %s")
        params.append(stage)

    if kind is not None:
        conditions.append("kind = %s")
        params.append(kind)

    if process_type is not None:
        conditions.append("process_type = %s")
        params.append(process_type)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    return store.execute_all(
        f"SELECT * FROM config_entries {where} ORDER BY priority DESC, key",
        *params
    )


def resolve_config(
    store: Store,
    service_id: int,
    stage: str = ConfigStage.APP,
) -> Dict[str, Any]:
    """解析配置（按优先级覆盖）

    返回: {
        key: {
            "value": str,
            "source": str,  # "platform/0", "project/1", "environment/2", "service/3"
            "priority": int,
            "kind": str,
            "process_type": str,
            "locked": bool,
        }
    }
    """
    # 1. 获取服务信息
    service = store.execute_one(
        "SELECT * FROM services WHERE id = %s",
        service_id
    )

    if not service:
        raise ValueError(f"服务 {service_id} 不存在")

    # 2. 获取项目和环境信息
    namespace = store.execute_one(
        "SELECT * FROM namespaces WHERE id = %s",
        service["namespace_id"]
    )

    # 3. 加载所有层级的配置
    all_configs = []

    # 3.1 平台配置
    all_configs.extend(list_config_entries(
        store,
        scope_type="platform",
        scope_id=0,
        stage=stage,
    ))

    # 3.2 项目配置
    all_configs.extend(list_config_entries(
        store,
        scope_type="project",
        scope_id=service["project_id"],
        stage=stage,
    ))

    # 3.3 环境配置
    all_configs.extend(list_config_entries(
        store,
        scope_type="environment",
        scope_id=service["namespace_id"],
        stage=stage,
    ))

    # 3.4 服务配置
    all_configs.extend(list_config_entries(
        store,
        scope_type="service",
        scope_id=service_id,
        stage=stage,
    ))

    # 4. 按优先级排序（高优先级在后，覆盖低优先级）
    all_configs.sort(key=lambda c: c["priority"])

    # 5. 合并配置（高优先级覆盖低优先级）
    result = {}
    locked_keys = set()

    for config in all_configs:
        key = config["key"]

        # 如果已锁定，跳过
        if key in locked_keys:
            continue

        # 记录配置
        result[key] = {
            "value": config["value"],
            "source": f"{config['scope_type']}/{config['scope_id']}",
            "priority": config["priority"],
            "kind": config["kind"],
            "process_type": config["process_type"],
            "locked": config["locked"],
        }

        # 如果锁定，记录
        if config["locked"]:
            locked_keys.add(key)

    return result


def create_config_snapshot(
    store: Store,
    deployment_id: int,
    service_id: int,
    config: Dict[str, Any],
    stage: str = ConfigStage.APP,
) -> int:
    """创建配置快照（用于 OBOM）"""
    # 计算配置哈希
    config_hash = _compute_config_hash(config)

    # 保存快照
    row = store.execute_one(
        """INSERT INTO config_snapshots
           (deployment_id, service_id, stage, snapshot_data, config_hash, created_at)
           VALUES (%s, %s, %s, %s, %s, NOW())
           RETURNING id""",
        deployment_id, service_id, stage, json.dumps(config), config_hash
    )

    return row["id"]


def get_config_snapshot(
    store: Store,
    deployment_id: int,
) -> Optional[Dict[str, Any]]:
    """获取配置快照"""
    row = store.execute_one(
        "SELECT * FROM config_snapshots WHERE deployment_id = %s",
        deployment_id
    )

    if not row:
        return None

    return {
        **row,
        "snapshot_data": json.loads(row["snapshot_data"]),
    }


def list_config_history(
    store: Store,
    *,
    config_entry_id: Optional[int] = None,
    scope_type: Optional[str] = None,
    scope_id: Optional[int] = None,
    key: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """列出配置变更历史"""
    conditions = []
    params = []

    if config_entry_id is not None:
        conditions.append("config_entry_id = %s")
        params.append(config_entry_id)

    if scope_type is not None:
        conditions.append("scope_type = %s")
        params.append(scope_type)

    if scope_id is not None:
        conditions.append("scope_id = %s")
        params.append(scope_id)

    if key is not None:
        conditions.append("key = %s")
        params.append(key)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    params.extend([limit, offset])

    return store.execute_all(
        f"SELECT * FROM config_entry_history {where} ORDER BY changed_at DESC LIMIT %s OFFSET %s",
        *params
    )


def apply_config_template(
    store: Store,
    template_id: int,
    scope_type: str,
    scope_id: int,
    *,
    created_by: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """应用配置模板"""
    # 1. 获取模板
    template = store.execute_one(
        "SELECT * FROM config_templates WHERE id = %s",
        template_id
    )

    if not template:
        raise ValueError(f"配置模板 {template_id} 不存在")

    # 2. 解析模板条目
    entries = json.loads(template["entries"])

    # 3. 创建配置条目
    created = []
    for entry in entries:
        config = create_config_entry(
            store,
            scope_type=scope_type,
            scope_id=scope_id,
            key=entry["key"],
            value=entry["value"],
            stage=template["stage"],
            kind=entry["kind"],
            process_type=entry["process_type"],
            description=entry.get("description", ""),
            created_by=created_by,
        )
        created.append(config)

    return created


def batch_create_configs(
    store: Store,
    scope_type: str,
    scope_id: int,
    configs: List[Dict[str, Any]],
    *,
    created_by: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """批量创建配置"""
    created = []
    for config in configs:
        entry = create_config_entry(
            store,
            scope_type=scope_type,
            scope_id=scope_id,
            key=config["key"],
            value=config["value"],
            stage=config.get("stage", ConfigStage.APP),
            kind=config.get("kind", ConfigKind.COMMON),
            process_type=config.get("process_type", ConfigProcessType.ENV),
            locked=config.get("locked", False),
            description=config.get("description", ""),
            created_by=created_by,
        )
        created.append(entry)

    return created


# === 内部辅助函数 ===

def _record_config_history(
    store: Store,
    *,
    config_entry_id: int,
    scope_type: str,
    scope_id: int,
    stage: str,
    priority: int,
    kind: str,
    process_type: str,
    key: str,
    old_value: Optional[str],
    new_value: Optional[str],
    action: str,
    changed_by: Optional[int],
    note: str = "",
) -> None:
    """记录配置变更历史"""
    store.execute(
        """INSERT INTO config_entry_history
           (config_entry_id, scope_type, scope_id, stage, priority, kind, process_type,
            key, old_value, new_value, action, changed_by, changed_at, note)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)""",
        config_entry_id, scope_type, scope_id, stage, priority, kind, process_type,
        key, old_value, new_value, action, changed_by, note
    )


def _compute_config_hash(config: Dict[str, Any]) -> str:
    """计算配置哈希"""
    # 按 key 排序后序列化
    sorted_config = {k: config[k] for k in sorted(config.keys())}
    config_str = json.dumps(sorted_config, sort_keys=True)
    return hashlib.sha256(config_str.encode()).hexdigest()
