"""配置生效机制：判断如何应用配置变更"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from ..store import Store
from ..store.enhanced_config import ConfigKind, ConfigProcessType


class ConfigEffectStrategy(Enum):
    """配置生效策略"""
    NO_ACTION = "no_action"  # 无需操作
    UPDATE_CONFIGMAP = "update_configmap"  # 更新 ConfigMap（热更新）
    UPDATE_SECRET = "update_secret"  # 更新 Secret
    RESTART_POD = "restart_pod"  # 重启 Pod（滚动更新）
    REDEPLOY = "redeploy"  # 完整重新发布


class ConfigChange:
    """配置变更"""

    def __init__(
        self,
        key: str,
        old_value: Optional[str],
        new_value: Optional[str],
        kind: str,
        process_type: str,
        action: str,  # create/update/delete
    ):
        self.key = key
        self.old_value = old_value
        self.new_value = new_value
        self.kind = kind
        self.process_type = process_type
        self.action = action

    @property
    def is_secret(self) -> bool:
        """是否是敏感配置"""
        return self.kind == ConfigKind.SECRET

    @property
    def is_file(self) -> bool:
        """是否是文件配置"""
        return self.process_type == ConfigProcessType.FILE

    @property
    def is_env(self) -> bool:
        """是否是环境变量"""
        return self.process_type == ConfigProcessType.ENV


class ConfigEffectResult:
    """配置生效结果"""

    def __init__(
        self,
        strategy: ConfigEffectStrategy,
        affected_keys: List[str],
        success: bool = True,
        message: str = "",
    ):
        self.strategy = strategy
        self.affected_keys = affected_keys
        self.success = success
        self.message = message


def determine_effect_strategy(
    changes: List[ConfigChange],
    *,
    support_hot_reload: bool = False,
) -> ConfigEffectStrategy:
    """确定配置生效策略

    Args:
        changes: 配置变更列表
        support_hot_reload: 应用是否支持配置热更新

    Returns:
        ConfigEffectStrategy: 生效策略
    """
    if not changes:
        return ConfigEffectStrategy.NO_ACTION

    # 策略优先级（从高到低）：
    # 1. 文件配置变更 → 重启 Pod（需要重新挂载卷）
    # 2. 敏感配置变更 → 更新 Secret
    # 3. 环境变量变更 + 不支持热更新 → 重启 Pod
    # 4. 环境变量变更 + 支持热更新 → 更新 ConfigMap

    has_file_change = any(c.is_file for c in changes)
    has_secret_change = any(c.is_secret for c in changes)
    has_env_change = any(c.is_env for c in changes)

    # 规则 1: 文件配置变更 → 必须重启
    if has_file_change:
        return ConfigEffectStrategy.RESTART_POD

    # 规则 2: 敏感环境变量变更 → 更新 Secret
    if has_secret_change and has_env_change:
        # 如果应用不支持热更新，还是需要重启
        if not support_hot_reload:
            return ConfigEffectStrategy.RESTART_POD
        return ConfigEffectStrategy.UPDATE_SECRET

    # 规则 3: 普通环境变量变更
    if has_env_change:
        if support_hot_reload:
            return ConfigEffectStrategy.UPDATE_CONFIGMAP
        else:
            return ConfigEffectStrategy.RESTART_POD

    return ConfigEffectStrategy.NO_ACTION


def apply_config_changes(
    store: Store,
    k8s_client: Any,
    service_id: int,
    changes: List[ConfigChange],
    *,
    support_hot_reload: bool = False,
) -> ConfigEffectResult:
    """应用配置变更

    Args:
        store: 数据库连接
        k8s_client: K8s 客户端
        service_id: 服务 ID
        changes: 配置变更列表
        support_hot_reload: 应用是否支持热更新

    Returns:
        ConfigEffectResult: 生效结果
    """
    from .config_objects import restart_deployment, update_configmap, update_secret

    # 1. 确定生效策略
    strategy = determine_effect_strategy(changes, support_hot_reload=support_hot_reload)

    # 2. 获取服务信息
    service = store.execute_one("SELECT * FROM services WHERE id = %s", service_id)
    if not service:
        return ConfigEffectResult(
            strategy=strategy,
            affected_keys=[],
            success=False,
            message=f"服务 {service_id} 不存在",
        )

    service_name = service["name"]
    namespace = store.execute_one(
        "SELECT name FROM namespaces WHERE id = %s",
        service["namespace_id"]
    )["name"]

    affected_keys = [c.key for c in changes]

    # 3. 执行策略
    try:
        if strategy == ConfigEffectStrategy.NO_ACTION:
            return ConfigEffectResult(
                strategy=strategy,
                affected_keys=affected_keys,
                success=True,
                message="无需操作",
            )

        elif strategy == ConfigEffectStrategy.UPDATE_CONFIGMAP:
            # 更新 ConfigMap
            _update_configmap_for_changes(
                store,
                k8s_client,
                service_id,
                namespace,
                service_name,
                changes,
            )
            return ConfigEffectResult(
                strategy=strategy,
                affected_keys=affected_keys,
                success=True,
                message=f"已更新 ConfigMap，{len(affected_keys)} 个配置项",
            )

        elif strategy == ConfigEffectStrategy.UPDATE_SECRET:
            # 更新 Secret
            _update_secret_for_changes(
                store,
                k8s_client,
                service_id,
                namespace,
                service_name,
                changes,
            )
            return ConfigEffectResult(
                strategy=strategy,
                affected_keys=affected_keys,
                success=True,
                message=f"已更新 Secret，{len(affected_keys)} 个配置项",
            )

        elif strategy == ConfigEffectStrategy.RESTART_POD:
            # 重启 Deployment（触发滚动更新）
            restart_deployment(k8s_client, namespace, service_name)
            return ConfigEffectResult(
                strategy=strategy,
                affected_keys=affected_keys,
                success=True,
                message=f"已触发滚动更新，{len(affected_keys)} 个配置项将在 Pod 重启后生效",
            )

        elif strategy == ConfigEffectStrategy.REDEPLOY:
            # 需要完整重新发布（通常不由配置变更触发）
            return ConfigEffectResult(
                strategy=strategy,
                affected_keys=affected_keys,
                success=False,
                message="需要完整重新发布",
            )

    except Exception as e:
        return ConfigEffectResult(
            strategy=strategy,
            affected_keys=affected_keys,
            success=False,
            message=f"应用配置失败: {str(e)}",
        )


def detect_config_changes(
    store: Store,
    service_id: int,
    new_configs: Dict[str, Dict[str, Any]],
) -> List[ConfigChange]:
    """检测配置变更

    Args:
        store: 数据库连接
        service_id: 服务 ID
        new_configs: 新配置（来自 resolve_config）

    Returns:
        配置变更列表
    """
    from ..store.enhanced_config import resolve_config

    # 1. 获取当前配置
    current_configs = resolve_config(store, service_id)

    # 2. 对比差异
    changes = []

    # 2.1 新增和修改
    for key, new_value_obj in new_configs.items():
        if key not in current_configs:
            # 新增
            changes.append(ConfigChange(
                key=key,
                old_value=None,
                new_value=new_value_obj["value"],
                kind=new_value_obj["kind"],
                process_type=new_value_obj["process_type"],
                action="create",
            ))
        elif current_configs[key]["value"] != new_value_obj["value"]:
            # 修改
            changes.append(ConfigChange(
                key=key,
                old_value=current_configs[key]["value"],
                new_value=new_value_obj["value"],
                kind=new_value_obj["kind"],
                process_type=new_value_obj["process_type"],
                action="update",
            ))

    # 2.2 删除
    for key, old_value_obj in current_configs.items():
        if key not in new_configs:
            changes.append(ConfigChange(
                key=key,
                old_value=old_value_obj["value"],
                new_value=None,
                kind=old_value_obj["kind"],
                process_type=old_value_obj["process_type"],
                action="delete",
            ))

    return changes


# === 内部辅助函数 ===

def _update_configmap_for_changes(
    store: Store,
    k8s_client: Any,
    service_id: int,
    namespace: str,
    service_name: str,
    changes: List[ConfigChange],
) -> None:
    """更新 ConfigMap（仅更新变更的配置）"""
    from ..store.enhanced_config import resolve_config
    from .config_objects import update_configmap

    # 1. 获取完整配置
    config = resolve_config(store, service_id)

    # 2. 提取环境变量配置
    env_vars = {}
    for key, value_obj in config.items():
        if (value_obj["process_type"] == ConfigProcessType.ENV and
            value_obj["kind"] == ConfigKind.COMMON):
            env_vars[key] = value_obj["value"]

    # 3. 更新 ConfigMap
    if env_vars:
        update_configmap(
            k8s_client,
            namespace,
            f"{service_name}-env",
            env_vars,
        )


def _update_secret_for_changes(
    store: Store,
    k8s_client: Any,
    service_id: int,
    namespace: str,
    service_name: str,
    changes: List[ConfigChange],
) -> None:
    """更新 Secret（仅更新变更的配置）"""
    from ..store.enhanced_config import resolve_config
    from .config_objects import update_secret

    # 1. 获取完整配置
    config = resolve_config(store, service_id)

    # 2. 提取敏感环境变量配置
    secret_env = {}
    for key, value_obj in config.items():
        if (value_obj["process_type"] == ConfigProcessType.ENV and
            value_obj["kind"] == ConfigKind.SECRET):
            secret_env[key] = value_obj["value"]

    # 3. 更新 Secret
    if secret_env:
        update_secret(
            k8s_client,
            namespace,
            f"{service_name}-env-secret",
            secret_env,
        )
