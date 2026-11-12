"""ConfigMap 和 Secret 生成逻辑：基于增强配置模型"""
from __future__ import annotations

import base64
from typing import Any, Dict, List, Tuple

from ..store import Store
from ..store.enhanced_config import ConfigKind, ConfigProcessType, resolve_config


class ConfigObjects:
    """配置对象集合"""

    def __init__(self):
        self.configmaps: List[Dict[str, Any]] = []
        self.secrets: List[Dict[str, Any]] = []

    def add_configmap(self, configmap: Dict[str, Any]) -> None:
        """添加 ConfigMap"""
        self.configmaps.append(configmap)

    def add_secret(self, secret: Dict[str, Any]) -> None:
        """添加 Secret"""
        self.secrets.append(secret)

    def all_objects(self) -> List[Dict[str, Any]]:
        """获取所有对象"""
        return self.configmaps + self.secrets


def generate_config_objects(
    store: Store,
    service_id: int,
    namespace: str,
    *,
    stage: str = "app",
    include_resource_exports: bool = True,
) -> ConfigObjects:
    """生成完整的 ConfigMap 和 Secret 对象

    生成 4 个对象：
    1. {service}-config ConfigMap - 配置文件（process_type=file, kind=common）
    2. {service}-env ConfigMap - 环境变量（process_type=env, kind=common）
    3. {service}-secret Secret - 敏感文件（process_type=file, kind=secret）
    4. {service}-env-secret Secret - 敏感环境变量（process_type=env, kind=secret）

    Args:
        store: 数据库连接
        service_id: 服务 ID
        namespace: K8s 命名空间
        stage: 配置阶段（init/app）
        include_resource_exports: 是否包含资源导出

    Returns:
        ConfigObjects: 包含所有 ConfigMap 和 Secret 的对象
    """
    # 1. 获取服务信息
    service = store.execute_one("SELECT * FROM services WHERE id = %s", service_id)
    if not service:
        raise ValueError(f"服务 {service_id} 不存在")

    service_name = service["name"]

    # 2. 解析配置（按优先级）
    config = resolve_config(store, service_id, stage)

    # 3. 分类配置
    env_vars: Dict[str, str] = {}  # 普通环境变量
    config_files: Dict[str, str] = {}  # 普通配置文件
    secret_env: Dict[str, str] = {}  # 敏感环境变量
    secret_files: Dict[str, str] = {}  # 敏感文件

    for key, value_obj in config.items():
        value = value_obj["value"]
        kind = value_obj["kind"]
        process_type = value_obj["process_type"]

        if process_type == ConfigProcessType.ENV:
            if kind == ConfigKind.SECRET:
                secret_env[key] = value
            else:
                env_vars[key] = value
        elif process_type == ConfigProcessType.FILE:
            if kind == ConfigKind.SECRET:
                secret_files[key] = value
            else:
                config_files[key] = value
        # process_type=template 的配置在渲染阶段使用，不生成 K8s 对象

    # 4. 添加资源导出
    if include_resource_exports:
        resource_exports = _get_resource_exports(store, service_id)
        env_vars.update(resource_exports.get("env", {}))
        secret_env.update(resource_exports.get("secret_env", {}))

    # 5. 生成对象
    objects = ConfigObjects()

    # 5.1 生成 {service}-config ConfigMap（配置文件）
    if config_files:
        objects.add_configmap({
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": f"{service_name}-config",
                "namespace": namespace,
                "labels": {
                    "app": service_name,
                    "managed-by": "swr-builder",
                    "config-type": "files",
                },
            },
            "data": config_files,
        })

    # 5.2 生成 {service}-env ConfigMap（环境变量）
    if env_vars:
        objects.add_configmap({
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": f"{service_name}-env",
                "namespace": namespace,
                "labels": {
                    "app": service_name,
                    "managed-by": "swr-builder",
                    "config-type": "env",
                },
            },
            "data": env_vars,
        })

    # 5.3 生成 {service}-secret Secret（敏感文件）
    if secret_files:
        objects.add_secret({
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": f"{service_name}-secret",
                "namespace": namespace,
                "labels": {
                    "app": service_name,
                    "managed-by": "swr-builder",
                    "config-type": "secret-files",
                },
            },
            "type": "Opaque",
            "stringData": secret_files,
        })

    # 5.4 生成 {service}-env-secret Secret（敏感环境变量）
    if secret_env:
        objects.add_secret({
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": f"{service_name}-env-secret",
                "namespace": namespace,
                "labels": {
                    "app": service_name,
                    "managed-by": "swr-builder",
                    "config-type": "secret-env",
                },
            },
            "type": "Opaque",
            "stringData": secret_env,
        })

    return objects


def generate_volume_mounts(
    service_name: str,
    has_config_files: bool = False,
    has_secret_files: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """生成 Volume 和 VolumeMount 配置

    Args:
        service_name: 服务名称
        has_config_files: 是否有配置文件
        has_secret_files: 是否有敏感文件

    Returns:
        (volumes, volumeMounts) 元组
    """
    volumes = []
    volume_mounts = []

    # 配置文件卷
    if has_config_files:
        volumes.append({
            "name": "config",
            "configMap": {
                "name": f"{service_name}-config",
            },
        })
        volume_mounts.append({
            "name": "config",
            "mountPath": "/app/config",
            "readOnly": True,
        })

    # 敏感文件卷
    if has_secret_files:
        volumes.append({
            "name": "secret",
            "secret": {
                "secretName": f"{service_name}-secret",
            },
        })
        volume_mounts.append({
            "name": "secret",
            "mountPath": "/app/secret",
            "readOnly": True,
        })

    return volumes, volume_mounts


def generate_env_from(
    service_name: str,
    has_env_vars: bool = False,
    has_secret_env: bool = False,
) -> List[Dict[str, Any]]:
    """生成 envFrom 配置

    Args:
        service_name: 服务名称
        has_env_vars: 是否有环境变量
        has_secret_env: 是否有敏感环境变量

    Returns:
        envFrom 列表
    """
    env_from = []

    # 普通环境变量
    if has_env_vars:
        env_from.append({
            "configMapRef": {
                "name": f"{service_name}-env",
            },
        })

    # 敏感环境变量
    if has_secret_env:
        env_from.append({
            "secretRef": {
                "name": f"{service_name}-env-secret",
            },
        })

    return env_from


def update_configmap(
    k8s_client: Any,
    namespace: str,
    name: str,
    data: Dict[str, str],
) -> Dict[str, Any]:
    """更新 ConfigMap（配置热更新）

    Args:
        k8s_client: K8s 客户端
        namespace: 命名空间
        name: ConfigMap 名称
        data: 新数据

    Returns:
        更新后的 ConfigMap
    """
    # 1. 获取现有 ConfigMap
    existing = k8s_client.get(
        kind="ConfigMap",
        name=name,
        namespace=namespace,
    )

    if not existing:
        raise ValueError(f"ConfigMap {namespace}/{name} 不存在")

    # 2. 更新数据
    existing["data"] = data

    # 3. 提交更新
    return k8s_client.update(
        kind="ConfigMap",
        name=name,
        namespace=namespace,
        body=existing,
    )


def update_secret(
    k8s_client: Any,
    namespace: str,
    name: str,
    data: Dict[str, str],
) -> Dict[str, Any]:
    """更新 Secret

    Args:
        k8s_client: K8s 客户端
        namespace: 命名空间
        name: Secret 名称
        data: 新数据（明文）

    Returns:
        更新后的 Secret
    """
    # 1. 获取现有 Secret
    existing = k8s_client.get(
        kind="Secret",
        name=name,
        namespace=namespace,
    )

    if not existing:
        raise ValueError(f"Secret {namespace}/{name} 不存在")

    # 2. 更新数据（base64 编码）
    existing["data"] = {
        key: base64.b64encode(value.encode()).decode()
        for key, value in data.items()
    }

    # 3. 提交更新
    return k8s_client.update(
        kind="Secret",
        name=name,
        namespace=namespace,
        body=existing,
    )


def restart_deployment(
    k8s_client: Any,
    namespace: str,
    name: str,
) -> Dict[str, Any]:
    """重启 Deployment（触发滚动更新）

    通过修改 spec.template.metadata.annotations 中的 restartedAt 时间戳触发滚动更新。

    Args:
        k8s_client: K8s 客户端
        namespace: 命名空间
        name: Deployment 名称

    Returns:
        更新后的 Deployment
    """
    from datetime import datetime, timezone

    # 生成重启时间戳
    restarted_at = datetime.now(timezone.utc).isoformat()

    # Patch Deployment
    return k8s_client.patch(
        kind="Deployment",
        name=name,
        namespace=namespace,
        body={
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "kubectl.kubernetes.io/restartedAt": restarted_at,
                        },
                    },
                },
            },
        },
    )


# === 内部辅助函数 ===

def _get_resource_exports(
    store: Store,
    service_id: int,
) -> Dict[str, Dict[str, str]]:
    """获取资源导出的环境变量

    Returns:
        {
            "env": {key: value},  # 普通环境变量
            "secret_env": {key: value},  # 敏感环境变量
        }
    """
    from ..delivery.resources import compute_exports

    # 1. 查询服务的资源绑定
    bindings = store.execute_all(
        """SELECT srb.*, r.type, r.attributes, r.parent_resource_id
           FROM service_resource_bindings srb
           JOIN resources r ON srb.resource_id = r.id
           WHERE srb.service_id = %s""",
        service_id
    )

    if not bindings:
        return {"env": {}, "secret_env": {}}

    # 2. 计算每个资源的导出
    all_env = {}
    all_secret_env = {}

    for binding in bindings:
        # 构建资源链（包括父资源）
        chain = _build_resource_chain(store, binding)

        # 计算导出
        try:
            exports = compute_exports(chain)

            # 添加别名前缀
            alias = binding["alias"].upper()
            for key, value in exports.get("env", []):
                prefixed_key = f"{alias}_{key}"
                all_env[prefixed_key] = value

            for key, value in exports.get("secret_env", []):
                prefixed_key = f"{alias}_{key}"
                all_secret_env[prefixed_key] = value
        except Exception:
            # 导出失败，跳过
            continue

    return {
        "env": all_env,
        "secret_env": all_secret_env,
    }


def _build_resource_chain(
    store: Store,
    binding: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """构建资源链（包括父资源）"""
    import json

    chain = []

    # 添加当前资源
    attributes = json.loads(binding["attributes"]) if isinstance(binding["attributes"], str) else binding["attributes"]
    chain.append({
        "id": binding["resource_id"],
        "type": binding["type"],
        "attributes": attributes,
    })

    # 添加父资源
    if binding["parent_resource_id"]:
        parent = store.execute_one(
            "SELECT * FROM resources WHERE id = %s",
            binding["parent_resource_id"]
        )
        if parent:
            parent_attributes = json.loads(parent["attributes"]) if isinstance(parent["attributes"], str) else parent["attributes"]
            chain.append({
                "id": parent["id"],
                "type": parent["type"],
                "attributes": parent_attributes,
            })

            # 添加祖父资源
            if parent.get("parent_resource_id"):
                grandparent = store.execute_one(
                    "SELECT * FROM resources WHERE id = %s",
                    parent["parent_resource_id"]
                )
                if grandparent:
                    grandparent_attributes = json.loads(grandparent["attributes"]) if isinstance(grandparent["attributes"], str) else grandparent["attributes"]
                    chain.append({
                        "id": grandparent["id"],
                        "type": grandparent["type"],
                        "attributes": grandparent_attributes,
                    })

    return chain
