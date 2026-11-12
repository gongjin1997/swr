"""资源类型定义、属性校验、父链展开、导出计算。

资源类型登记在 RESOURCE_TYPES 中，每种类型声明：
- attributes: 属性字段定义 {name: {type, secret, required}}
- parent_type: 父类型名称（可选），用于构建父子链
- exports: 导出规则列表，每条规则指定 kind、key 模式和值模式

导出规则支持 {属性名} 占位符替换，例如：
- key: "j2c.opengauss_dbuser.{user}.password" → secret_env
- value: "jdbc:postgresql://{host}:{port}/{database}" → files:properties 的 spring.datasource.url
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# 导出 kind：对应配置集的 kind
EXPORT_KINDS = {"env", "secret_env", "files", "secret_files"}

# 属性类型
ATTR_TYPES = {"string", "integer", "boolean"}


def _gauss_db_exports() -> List[Dict[str, Any]]:
    """GaussDB 数据库实例导出规则"""
    return [
        {"kind": "files", "subpath": "properties", "key": "spring.datasource.gauss.host", "value": "{host}"},
        {"kind": "files", "subpath": "properties", "key": "spring.datasource.gauss.port", "value": "{port}"},
    ]


def _gauss_schema_exports() -> List[Dict[str, Any]]:
    """GaussDB schema 导出规则"""
    return [
        {"kind": "files", "subpath": "properties", "key": "spring.datasource.gauss.database", "value": "{database}"},
        {"kind": "files", "subpath": "properties", "key": "spring.datasource.gauss.schema", "value": "{schema}"},
    ]


def _gauss_user_exports() -> List[Dict[str, Any]]:
    """GaussDB 用户导出规则"""
    return [
        {"kind": "files", "subpath": "properties", "key": "spring.datasource.gauss.username", "value": "{user}"},
        {"kind": "secret_env", "key": "j2c.opengauss_dbuser.{user}.password", "value": "{password}"},
        {"kind": "files", "subpath": "properties", "key": "spring.datasource.url",
         "value": "jdbc:postgresql://{host}:{port}/{database}?currentSchema={schema}"},
        {"kind": "files", "subpath": "properties", "key": "spring.datasource.username", "value": "{user}"},
    ]


def _redis_exports() -> List[Dict[str, Any]]:
    """Redis 导出规则"""
    return [
        {"kind": "env", "key": "REDIS_HOST", "value": "{host}"},
        {"kind": "env", "key": "REDIS_PORT", "value": "{port}"},
        {"kind": "secret_env", "key": "REDIS_PASSWORD", "value": "{password}"},
        {"kind": "files", "subpath": "properties", "key": "spring.redis.host", "value": "{host}"},
        {"kind": "files", "subpath": "properties", "key": "spring.redis.port", "value": "{port}"},
    ]


def _kafka_exports() -> List[Dict[str, Any]]:
    """Kafka 导出规则"""
    return [
        {"kind": "env", "key": "KAFKA_BROKERS", "value": "{brokers}"},
        {"kind": "files", "subpath": "properties", "key": "spring.kafka.bootstrap-servers", "value": "{brokers}"},
    ]


def _mqs_exports() -> List[Dict[str, Any]]:
    """MQS（RocketMQ）导出规则"""
    return [
        {"kind": "files", "subpath": "properties", "key": "erpmq.mqs.nameserver", "value": "{nameserver}"},
        {"kind": "files", "subpath": "properties", "key": "erpmq.mqs.accesskey", "value": "{access_key}"},
        {"kind": "secret_env", "key": "erpmq.mqs.secretkey", "value": "{secret_key}"},
    ]


def _config_center_exports() -> List[Dict[str, Any]]:
    """配置中心导出规则"""
    return [
        {"kind": "env", "key": "configcenter_url", "value": "{url}"},
    ]


def _iam_endpoint_exports() -> List[Dict[str, Any]]:
    """IAM 鉴权端点导出规则"""
    return [
        {"kind": "env", "key": "service_authentication_url", "value": "{auth_url}"},
        {"kind": "env", "key": "service_authorization_url", "value": "{authz_url}"},
        {"kind": "files", "subpath": "properties", "key": "iam.endpoint", "value": "{endpoint}"},
        {"kind": "files", "subpath": "properties", "key": "iam.account", "value": "{account}"},
    ]


def _endpoint_exports() -> List[Dict[str, Any]]:
    """通用端点导出规则"""
    return [
        {"kind": "env", "key": "{name}_URL", "value": "{url}"},
    ]


# 资源类型注册表
RESOURCE_TYPES: Dict[str, Dict[str, Any]] = {
    "gauss_db": {
        "label": "GaussDB 数据库",
        "attributes": {
            "host": {"type": "string", "secret": False, "required": True, "label": "主机"},
            "port": {"type": "integer", "secret": False, "required": True, "label": "端口", "default": 5432},
        },
        "parent_type": None,
        "exports": _gauss_db_exports(),
    },
    "gauss_schema": {
        "label": "GaussDB Schema",
        "attributes": {
            "database": {"type": "string", "secret": False, "required": True, "label": "数据库名"},
            "schema": {"type": "string", "secret": False, "required": True, "label": "Schema 名"},
        },
        "parent_type": "gauss_db",
        "exports": _gauss_schema_exports(),
    },
    "gauss_user": {
        "label": "GaussDB 用户",
        "attributes": {
            "user": {"type": "string", "secret": False, "required": True, "label": "用户名"},
            "password": {"type": "string", "secret": True, "required": True, "label": "密码"},
        },
        "parent_type": "gauss_schema",
        "exports": _gauss_user_exports(),
    },
    "redis": {
        "label": "Redis",
        "attributes": {
            "host": {"type": "string", "secret": False, "required": True, "label": "主机"},
            "port": {"type": "integer", "secret": False, "required": True, "label": "端口", "default": 6379},
            "password": {"type": "string", "secret": True, "required": False, "label": "密码"},
        },
        "parent_type": None,
        "exports": _redis_exports(),
    },
    "kafka": {
        "label": "Kafka",
        "attributes": {
            "brokers": {"type": "string", "secret": False, "required": True, "label": "Broker 地址"},
        },
        "parent_type": None,
        "exports": _kafka_exports(),
    },
    "mqs": {
        "label": "MQS (RocketMQ)",
        "attributes": {
            "nameserver": {"type": "string", "secret": False, "required": True, "label": "NameServer 地址"},
            "access_key": {"type": "string", "secret": False, "required": True, "label": "AccessKey"},
            "secret_key": {"type": "string", "secret": True, "required": True, "label": "SecretKey"},
        },
        "parent_type": None,
        "exports": _mqs_exports(),
    },
    "config_center": {
        "label": "配置中心",
        "attributes": {
            "url": {"type": "string", "secret": False, "required": True, "label": "服务地址"},
        },
        "parent_type": None,
        "exports": _config_center_exports(),
    },
    "iam_endpoint": {
        "label": "IAM 鉴权端点",
        "attributes": {
            "endpoint": {"type": "string", "secret": False, "required": True, "label": "端点地址"},
            "auth_url": {"type": "string", "secret": False, "required": True, "label": "认证地址"},
            "authz_url": {"type": "string", "secret": False, "required": True, "label": "授权地址"},
            "account": {"type": "string", "secret": False, "required": True, "label": "账号"},
        },
        "parent_type": None,
        "exports": _iam_endpoint_exports(),
    },
    "endpoint": {
        "label": "通用端点",
        "attributes": {
            "name": {"type": "string", "secret": False, "required": True, "label": "服务名"},
            "url": {"type": "string", "secret": False, "required": True, "label": "服务地址"},
        },
        "parent_type": None,
        "exports": _endpoint_exports(),
    },
}


def validate_type(type_name: str) -> None:
    """校验资源类型是否已注册"""
    if type_name not in RESOURCE_TYPES:
        raise ValueError(f"未知资源类型: {type_name}")


def validate_attributes(type_name: str, attrs: Dict[str, Any]) -> Dict[str, Any]:
    """校验并规范化属性值，返回规范化后的属性字典"""
    validate_type(type_name)
    spec = RESOURCE_TYPES[type_name]
    normalized = {}
    for name, attr_spec in spec["attributes"].items():
        value = attrs.get(name)
        if value is None:
            if attr_spec["required"]:
                if "default" in attr_spec:
                    value = attr_spec["default"]
                else:
                    raise ValueError(f"缺少必填属性: {name}")
            else:
                continue
        # 类型校验
        if attr_spec["type"] == "string":
            if not isinstance(value, str):
                raise ValueError(f"属性 {name} 必须是字符串")
            normalized[name] = value
        elif attr_spec["type"] == "integer":
            if isinstance(value, str):
                try:
                    normalized[name] = int(value)
                except ValueError:
                    raise ValueError(f"属性 {name} 必须是整数")
            elif isinstance(value, int):
                normalized[name] = value
            else:
                raise ValueError(f"属性 {name} 必须是整数")
        elif attr_spec["type"] == "boolean":
            if isinstance(value, bool):
                normalized[name] = value
            elif isinstance(value, str):
                normalized[name] = value.lower() in ("true", "1", "yes")
            else:
                raise ValueError(f"属性 {name} 必须是布尔值")
    # 检查额外属性
    extra = set(attrs.keys()) - set(spec["attributes"].keys())
    if extra:
        raise ValueError(f"未知属性: {', '.join(extra)}")
    return normalized


def secret_attributes(type_name: str) -> List[str]:
    """返回该类型的 secret 属性名列表"""
    validate_type(type_name)
    spec = RESOURCE_TYPES[type_name]
    return [name for name, attr_spec in spec["attributes"].items() if attr_spec["secret"]]


def parent_chain(resources_by_id: Dict[int, Dict[str, Any]], resource_id: int) -> List[Dict[str, Any]]:
    """展开资源的父链，返回 [自己, 父, 祖父, ...]"""
    chain = []
    current_id = resource_id
    seen = set()
    while current_id is not None:
        if current_id in seen:
            raise ValueError(f"资源父链出现循环: {current_id}")
        seen.add(current_id)
        resource = resources_by_id.get(current_id)
        if not resource:
            raise ValueError(f"资源不存在: {current_id}")
        chain.append(resource)
        current_id = resource.get("parent_id")
    return chain


def _substitute(pattern: str, attrs: Dict[str, Any]) -> str:
    """替换模式中的 {属性名} 占位符"""
    def replace(match: re.Match) -> str:
        key = match.group(1)
        if key not in attrs:
            raise ValueError(f"导出规则引用了不存在的属性: {key}")
        value = attrs[key]
        return str(value)
    return re.sub(r'\{(\w+)\}', replace, pattern)


def compute_exports(chain: List[Dict[str, Any]]) -> Dict[str, List[Tuple[str, str, Optional[str]]]]:
    """
    计算资源链的导出配置。

    参数:
        chain: 资源链 [子, 父, 祖父, ...]，每个元素包含 type 和 attributes

    返回:
        按 kind 分组的导出列表: {kind: [(key, value, subpath), ...]}
        其中 subpath 仅对 files/secret_files 类型有值
    """
    result: Dict[str, List[Tuple[str, str, Optional[str]]]] = {k: [] for k in EXPORT_KINDS}

    # 合并所有祖先的属性，子优先
    all_attrs = {}
    for resource in reversed(chain):
        all_attrs.update(resource["attributes"])

    # 收集所有导出规则
    for resource in chain:
        type_name = resource["type"]
        spec = RESOURCE_TYPES[type_name]
        for export in spec["exports"]:
            kind = export["kind"]
            key_pattern = export["key"]
            value_pattern = export["value"]
            subpath = export.get("subpath")

            try:
                key = _substitute(key_pattern, all_attrs)
                value = _substitute(value_pattern, all_attrs)
                result[kind].append((key, value, subpath))
            except ValueError as e:
                # 属性不足时跳过该导出规则（例如可选属性未提供）
                continue

    return result


def mask_secrets(type_name: str, attrs: Dict[str, Any]) -> Dict[str, Any]:
    """屏蔽 secret 属性，返回屏蔽后的字典"""
    secret_keys = secret_attributes(type_name)
    return {k: ("******" if k in secret_keys else v) for k, v in attrs.items()}
