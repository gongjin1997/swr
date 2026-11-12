"""资源类型注册与导出规则（纯函数）。

资源类型定义：
- type: 类型标识符
- label: 显示名称
- attributes: 属性定义 {name: {type, secret, required, label, default?}}
- parent: 父资源类型（可选）
- exports: 导出规则列表 [{kind, key, value}]，kind 只支持 env / secret_env

导出规则中的 value 可以包含 {属性名} 占位符，从资源及其父链中替换。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from ..security import SecretBox

# 属性类型
ATTR_TYPES = {"string", "integer", "boolean"}

# 导出目标类型（仅支持 env 和 secret_env，按键合并）
EXPORT_KINDS = {"env", "secret_env"}

# 占位符模式
_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _gauss_db_exports() -> List[Dict[str, str]]:
    """GaussDB 数据库实例导出规则"""
    return [
        {"kind": "env", "key": "DB_HOST", "value": "{host}"},
        {"kind": "env", "key": "DB_PORT", "value": "{port}"},
    ]


def _gauss_schema_exports() -> List[Dict[str, str]]:
    """GaussDB schema 导出规则"""
    return [
        {"kind": "env", "key": "DB_NAME", "value": "{database}"},
        {"kind": "env", "key": "DB_SCHEMA", "value": "{schema}"},
    ]


def _gauss_user_exports() -> List[Dict[str, str]]:
    """GaussDB 用户导出规则"""
    return [
        {"kind": "env", "key": "DB_USER", "value": "{user}"},
        {"kind": "secret_env", "key": "DB_PASSWORD", "value": "{password}"},
        {"kind": "env", "key": "DB_URL", "value": "postgresql://{user}@{host}:{port}/{database}?currentSchema={schema}"},
    ]


def _redis_exports() -> List[Dict[str, str]]:
    """Redis 导出规则"""
    return [
        {"kind": "env", "key": "REDIS_HOST", "value": "{host}"},
        {"kind": "env", "key": "REDIS_PORT", "value": "{port}"},
        {"kind": "secret_env", "key": "REDIS_PASSWORD", "value": "{password}"},
    ]


def _kafka_exports() -> List[Dict[str, str]]:
    """Kafka 导出规则"""
    return [
        {"kind": "env", "key": "KAFKA_BROKERS", "value": "{brokers}"},
    ]


def _mqs_exports() -> List[Dict[str, str]]:
    """MQS（RocketMQ）导出规则"""
    return [
        {"kind": "env", "key": "MQS_NAMESERVER", "value": "{nameserver}"},
        {"kind": "env", "key": "MQS_ACCESS_KEY", "value": "{access_key}"},
        {"kind": "secret_env", "key": "MQS_SECRET_KEY", "value": "{secret_key}"},
    ]


def _config_center_exports() -> List[Dict[str, str]]:
    """配置中心导出规则"""
    return [
        {"kind": "env", "key": "CONFIG_CENTER_URL", "value": "{url}"},
    ]


def _iam_endpoint_exports() -> List[Dict[str, str]]:
    """IAM 鉴权端点导出规则"""
    return [
        {"kind": "env", "key": "IAM_ENDPOINT", "value": "{endpoint}"},
        {"kind": "env", "key": "IAM_AUTH_URL", "value": "{auth_url}"},
        {"kind": "env", "key": "IAM_AUTHZ_URL", "value": "{authz_url}"},
        {"kind": "env", "key": "IAM_ACCOUNT", "value": "{account}"},
    ]


def _endpoint_exports() -> List[Dict[str, str]]:
    """通用端点导出规则"""
    return [
        {"kind": "env", "key": "{name}_URL", "value": "{url}"},
    ]


RESOURCE_TYPES: Dict[str, Dict[str, Any]] = {
    "gauss_db": {
        "label": "GaussDB 数据库",
        "parent": None,
        "attributes": {
            "host": {"type": "string", "secret": False, "required": True, "label": "主机"},
            "port": {"type": "integer", "secret": False, "required": True, "label": "端口", "default": 5432},
        },
        "exports": _gauss_db_exports(),
    },
    "gauss_schema": {
        "label": "GaussDB Schema",
        "parent": "gauss_db",
        "attributes": {
            "database": {"type": "string", "secret": False, "required": True, "label": "数据库名"},
            "schema": {"type": "string", "secret": False, "required": True, "label": "Schema 名"},
        },
        "exports": _gauss_schema_exports(),
    },
    "gauss_user": {
        "label": "GaussDB 用户",
        "parent": "gauss_schema",
        "attributes": {
            "user": {"type": "string", "secret": False, "required": True, "label": "用户名"},
            "password": {"type": "string", "secret": True, "required": True, "label": "密码"},
        },
        "exports": _gauss_user_exports(),
    },
    "redis": {
        "label": "Redis",
        "parent": None,
        "attributes": {
            "host": {"type": "string", "secret": False, "required": True, "label": "主机"},
            "port": {"type": "integer", "secret": False, "required": True, "label": "端口", "default": 6379},
            "password": {"type": "string", "secret": True, "required": False, "label": "密码"},
        },
        "exports": _redis_exports(),
    },
    "kafka": {
        "label": "Kafka",
        "parent": None,
        "attributes": {
            "brokers": {"type": "string", "secret": False, "required": True, "label": "Broker 地址"},
        },
        "exports": _kafka_exports(),
    },
    "mqs": {
        "label": "MQS (RocketMQ)",
        "parent": None,
        "attributes": {
            "nameserver": {"type": "string", "secret": False, "required": True, "label": "NameServer 地址"},
            "access_key": {"type": "string", "secret": False, "required": True, "label": "AccessKey"},
            "secret_key": {"type": "string", "secret": True, "required": True, "label": "SecretKey"},
        },
        "exports": _mqs_exports(),
    },
    "config_center": {
        "label": "配置中心",
        "parent": None,
        "attributes": {
            "url": {"type": "string", "secret": False, "required": True, "label": "服务地址"},
        },
        "exports": _config_center_exports(),
    },
    "iam_endpoint": {
        "label": "IAM 鉴权端点",
        "parent": None,
        "attributes": {
            "endpoint": {"type": "string", "secret": False, "required": True, "label": "端点地址"},
            "auth_url": {"type": "string", "secret": False, "required": True, "label": "认证地址"},
            "authz_url": {"type": "string", "secret": False, "required": True, "label": "授权地址"},
            "account": {"type": "string", "secret": False, "required": True, "label": "账号"},
        },
        "exports": _iam_endpoint_exports(),
    },
    "endpoint": {
        "label": "通用端点",
        "parent": None,
        "attributes": {
            "name": {"type": "string", "secret": False, "required": True, "label": "服务名"},
            "url": {"type": "string", "secret": False, "required": True, "label": "服务地址"},
        },
        "exports": _endpoint_exports(),
    },
}


def validate_type(type_name: str) -> None:
    """校验资源类型是否已注册"""
    if type_name not in RESOURCE_TYPES:
        raise ValueError(f"不支持的资源类型：{type_name}")


def validate_attributes(type_name: str, attrs: Dict[str, Any], box: Optional[SecretBox] = None) -> Dict[str, Any]:
    """校验并规范化属性值，返回规范化后的属性字典（敏感属性已加密）"""
    validate_type(type_name)
    spec = RESOURCE_TYPES[type_name]
    attr_defs = spec["attributes"]

    result: Dict[str, Any] = {}
    for name, definition in attr_defs.items():
        value = attrs.get(name)
        required = definition.get("required", False)

        # 必填检查
        if required and (value is None or value == ""):
            raise ValueError(f"属性 {definition['label']} ({name}) 为必填项")

        # 可选属性未提供时使用默认值或跳过
        if value is None or value == "":
            if "default" in definition:
                value = definition["default"]
            else:
                continue

        # 类型检查与转换
        attr_type = definition["type"]
        if attr_type == "string":
            if not isinstance(value, str):
                raise ValueError(f"属性 {definition['label']} ({name}) 必须是字符串")
            result[name] = value
        elif attr_type == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"属性 {definition['label']} ({name}) 必须是整数")
            result[name] = value
        elif attr_type == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"属性 {definition['label']} ({name}) 必须是布尔值")
            result[name] = value
        else:
            raise ValueError(f"不支持的属性类型：{attr_type}")

    # 加密敏感属性
    if box:
        for name, definition in attr_defs.items():
            if definition.get("secret") and name in result:
                result[name] = box.encrypt(str(result[name]))

    return result


def secret_attributes(type_name: str) -> List[str]:
    """返回该类型的敏感属性名列表"""
    validate_type(type_name)
    spec = RESOURCE_TYPES[type_name]
    return [name for name, attr in spec["attributes"].items() if attr.get("secret")]


def parent_chain(resources_by_id: Dict[int, Dict[str, Any]], resource_id: int) -> List[Dict[str, Any]]:
    """展开资源的父链，返回 [自己, 父, 祖父, ...]"""
    chain: List[Dict[str, Any]] = []
    current_id: Optional[int] = resource_id
    visited = set()

    while current_id is not None:
        if current_id in visited:
            raise ValueError("资源父链中存在循环引用")
        visited.add(current_id)

        resource = resources_by_id.get(current_id)
        if not resource:
            raise ValueError(f"资源 ID {current_id} 不存在")

        chain.append(resource)

        # 查找父资源
        type_name = resource["type"]
        validate_type(type_name)
        parent_type = RESOURCE_TYPES[type_name].get("parent")

        if not parent_type:
            break

        # 父资源应该在同一作用域
        parent_resource = next((r for r in resources_by_id.values()
                               if r["type"] == parent_type
                               and r["scope_type"] == resource["scope_type"]
                               and r["scope_id"] == resource["scope_id"]), None)
        if not parent_resource:
            raise ValueError(f"资源 {resource['name']} 缺少父资源（类型 {parent_type}）")

        current_id = parent_resource["id"]

    return chain


def _substitute(pattern: str, attrs: Dict[str, Any]) -> str:
    """替换模式中的 {属性名} 占位符"""
    def replace(match: re.Match) -> str:
        key = match.group(1)
        value = attrs.get(key)
        if value is None:
            raise ValueError(f"导出规则引用了不存在的属性：{key}")
        return str(value)

    return _PLACEHOLDER.sub(replace, pattern)


def compute_exports(chain: List[Dict[str, Any]]) -> Dict[str, List[Tuple[str, str]]]:
    """
    计算资源链的导出配置。

    Args:
        chain: 资源链，[自己, 父, 祖父, ...]，每个资源包含 type 和 attributes（已解密）

    Returns:
        {kind: [(key, value)]}，kind 为 env 或 secret_env
    """
    # 收集所有属性（近到远，子覆盖父）
    all_attrs: Dict[str, Any] = {}
    for resource in reversed(chain):
        all_attrs.update(resource.get("attributes") or {})

    # 计算导出
    exports: Dict[str, List[Tuple[str, str]]] = {"env": [], "secret_env": []}

    for resource in chain:
        type_name = resource["type"]
        validate_type(type_name)
        spec = RESOURCE_TYPES[type_name]

        for export in spec["exports"]:
            kind = export["kind"]
            if kind not in EXPORT_KINDS:
                continue

            key_pattern = export["key"]
            value_pattern = export["value"]

            try:
                key = _substitute(key_pattern, all_attrs)
                value = _substitute(value_pattern, all_attrs)
                exports[kind].append((key, value))
            except ValueError as e:
                # 导出规则引用的属性不存在时跳过（可能是可选属性未填写）
                continue

    return exports


def mask_secrets(type_name: str, attrs: Dict[str, Any]) -> Dict[str, Any]:
    """屏蔽敏感属性，返回屏蔽后的字典"""
    secret_keys = secret_attributes(type_name)
    return {k: ("******" if k in secret_keys else v) for k, v in attrs.items()}


def checksum(attrs: Dict[str, Any]) -> str:
    """计算属性的校验和"""
    return hashlib.sha256(json.dumps(attrs, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
