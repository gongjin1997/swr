"""资源类型注册与导出规则（纯函数）。

资源类型定义：type, label, attributes {name: {type, secret, required, label, default?}}, parent, exports。
导出规则中的 value 可以包含 {属性名} 占位符，从资源及其父链中替换。env 导出不能引用敏感属性。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Tuple

ATTR_TYPES = {"string", "integer", "boolean"}
EXPORT_KINDS = {"env", "secret_env"}
_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_ALIAS_RE = re.compile(r"[A-Z][A-Z0-9_]{0,62}")

RESOURCE_TYPES: Dict[str, Dict[str, Any]] = {
    "gauss_db": {
        "label": "GaussDB 数据库",
        "parent": None,
        "attributes": {
            "host": {"type": "string", "secret": False, "required": True, "label": "主机"},
            "port": {"type": "integer", "secret": False, "required": True, "label": "端口", "default": 5432},
        },
        "exports": [
            {"kind": "env", "key": "DB_HOST", "value": "{host}"},
            {"kind": "env", "key": "DB_PORT", "value": "{port}"},
        ],
    },
    "gauss_schema": {
        "label": "GaussDB Schema",
        "parent": "gauss_db",
        "attributes": {
            "database": {"type": "string", "secret": False, "required": True, "label": "数据库名"},
            "schema": {"type": "string", "secret": False, "required": True, "label": "Schema 名"},
        },
        "exports": [
            {"kind": "env", "key": "DB_NAME", "value": "{database}"},
            {"kind": "env", "key": "DB_SCHEMA", "value": "{schema}"},
        ],
    },
    "gauss_user": {
        "label": "GaussDB 用户",
        "parent": "gauss_schema",
        "attributes": {
            "user": {"type": "string", "secret": False, "required": True, "label": "用户名"},
            "password": {"type": "string", "secret": True, "required": True, "label": "密码"},
        },
        "exports": [
            {"kind": "env", "key": "DB_USER", "value": "{user}"},
            {"kind": "secret_env", "key": "DB_PASSWORD", "value": "{password}"},
            {"kind": "env", "key": "DB_URL",
             "value": "postgresql://{user}@{host}:{port}/{database}?currentSchema={schema}"},
        ],
    },
    "redis": {
        "label": "Redis",
        "parent": None,
        "attributes": {
            "host": {"type": "string", "secret": False, "required": True, "label": "主机"},
            "port": {"type": "integer", "secret": False, "required": True, "label": "端口", "default": 6379},
            "password": {"type": "string", "secret": True, "required": False, "label": "密码"},
        },
        "exports": [
            {"kind": "env", "key": "REDIS_HOST", "value": "{host}"},
            {"kind": "env", "key": "REDIS_PORT", "value": "{port}"},
            {"kind": "secret_env", "key": "REDIS_PASSWORD", "value": "{password}"},
        ],
    },
    "kafka": {
        "label": "Kafka",
        "parent": None,
        "attributes": {
            "brokers": {"type": "string", "secret": False, "required": True, "label": "Broker 地址"},
        },
        "exports": [
            {"kind": "env", "key": "KAFKA_BROKERS", "value": "{brokers}"},
        ],
    },
    "mqs": {
        "label": "MQS (RocketMQ)",
        "parent": None,
        "attributes": {
            "nameserver": {"type": "string", "secret": False, "required": True, "label": "NameServer 地址"},
            "access_key": {"type": "string", "secret": False, "required": True, "label": "AccessKey"},
            "secret_key": {"type": "string", "secret": True, "required": True, "label": "SecretKey"},
        },
        "exports": [
            {"kind": "env", "key": "MQS_NAMESERVER", "value": "{nameserver}"},
            {"kind": "env", "key": "MQS_ACCESS_KEY", "value": "{access_key}"},
            {"kind": "secret_env", "key": "MQS_SECRET_KEY", "value": "{secret_key}"},
        ],
    },
    "config_center": {
        "label": "配置中心",
        "parent": None,
        "attributes": {
            "url": {"type": "string", "secret": False, "required": True, "label": "服务地址"},
        },
        "exports": [
            {"kind": "env", "key": "CONFIG_CENTER_URL", "value": "{url}"},
        ],
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
        "exports": [
            {"kind": "env", "key": "IAM_ENDPOINT", "value": "{endpoint}"},
            {"kind": "env", "key": "IAM_AUTH_URL", "value": "{auth_url}"},
            {"kind": "env", "key": "IAM_AUTHZ_URL", "value": "{authz_url}"},
            {"kind": "env", "key": "IAM_ACCOUNT", "value": "{account}"},
        ],
    },
    "endpoint": {
        "label": "通用端点",
        "parent": None,
        "attributes": {
            "name": {"type": "string", "secret": False, "required": True, "label": "服务名"},
            "url": {"type": "string", "secret": False, "required": True, "label": "服务地址"},
        },
        "exports": [
            {"kind": "env", "key": "{name}_URL", "value": "{url}"},
        ],
    },
}


def validate_type(type_name: str) -> None:
    if type_name not in RESOURCE_TYPES:
        raise ValueError(f"不支持的资源类型：{type_name}")


def validate_attributes(type_name: str, attrs: Dict[str, Any], previous: Optional[Dict[str, Any]] = None,
                       box: Optional[Any] = None) -> Dict[str, Any]:
    """校验并规范化属性。previous 为更新时的旧属性；敏感属性缺失时保留旧值（需要 box 解密计算 size）。"""
    validate_type(type_name)
    spec = RESOURCE_TYPES[type_name]
    attr_defs = spec["attributes"]
    unknown = set(attrs) - set(attr_defs)
    if unknown:
        raise ValueError(f"不支持的属性：{'、'.join(sorted(unknown))}")
    result: Dict[str, Any] = {}
    for name, definition in attr_defs.items():
        value = attrs.get(name)
        is_secret = definition.get("secret", False)
        if value is None or value == "":
            if is_secret and previous and name in previous:
                result[name] = previous[name]
                continue
            if "default" in definition:
                value = definition["default"]
            elif definition.get("required", False):
                raise ValueError(f"属性 {definition['label']} ({name}) 为必填项")
            else:
                continue
        attr_type = definition["type"]
        if attr_type == "string":
            if not isinstance(value, str):
                raise ValueError(f"属性 {definition['label']} ({name}) 必须是字符串")
        elif attr_type == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"属性 {definition['label']} ({name}) 必须是整数")
        elif attr_type == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"属性 {definition['label']} ({name}) 必须是布尔值")
        else:
            raise ValueError(f"不支持的属性类型：{attr_type}")
        if is_secret and box:
            value = box.encrypt(str(value))
        result[name] = value
    return result


def secret_attributes(type_name: str) -> List[str]:
    validate_type(type_name)
    return [name for name, attr in RESOURCE_TYPES[type_name]["attributes"].items() if attr.get("secret")]


def secret_size(type_name: str, attributes: Dict[str, Any], box: Any) -> Dict[str, int]:
    """计算敏感属性的明文长度（用于 reveal=False 的占位）。attributes 中的敏感值是密文。"""
    sizes: Dict[str, int] = {}
    for name in secret_attributes(type_name):
        if name not in attributes:
            continue
        ciphertext = attributes[name]
        if not ciphertext or not ciphertext.startswith("enc:"):
            sizes[name] = 0
            continue
        try:
            plaintext = box.decrypt(ciphertext)
            sizes[name] = len(plaintext.encode("utf-8"))
        except Exception:
            sizes[name] = 8
    return sizes


def mask_secrets(type_name: str, attrs: Dict[str, Any], sizes: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """屏蔽敏感属性。sizes 给定时显示"已设置，N 字节"；否则显示 ******。"""
    secret_keys = set(secret_attributes(type_name))
    result = {}
    for k, v in attrs.items():
        if k in secret_keys:
            if sizes and k in sizes:
                result[k] = f"已设置，{sizes[k]} 字节"
            else:
                result[k] = "******"
        else:
            result[k] = v
    return result


def validate_alias(alias: str) -> None:
    if alias and not _ALIAS_RE.fullmatch(alias):
        raise ValueError("别名必须为空或大写字母、数字、下划线（首字符大写字母，最多 63 字符）")


def checksum(attrs: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(attrs, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def compute_exports(chain: List[Dict[str, Any]], alias: str, box: Any) -> Dict[str, List[Tuple[str, str]]]:
    """计算资源链的导出。chain: [自己, 父, 祖父, ...]，每个包含 type 和 attributes（密文）。返回 {kind: [(key, value)]}。"""
    all_attrs: Dict[str, Any] = {}
    secret_keys: set[str] = set()
    for resource in reversed(chain):
        type_name = resource["type"]
        validate_type(type_name)
        attrs = resource.get("attributes") or {}
        for name in secret_attributes(type_name):
            if name in attrs:
                secret_keys.add(name)
                ciphertext = attrs[name]
                if ciphertext and ciphertext.startswith("enc:"):
                    try:
                        attrs[name] = box.decrypt(ciphertext)
                    except Exception:
                        raise ValueError(f"资源 {resource.get('name', '?')} 的敏感属性 {name} 解密失败")
        all_attrs.update(attrs)
    exports: Dict[str, List[Tuple[str, str]]] = {"env": [], "secret_env": []}
    for resource in chain:
        spec = RESOURCE_TYPES[resource["type"]]
        for export in spec["exports"]:
            kind = export["kind"]
            if kind not in EXPORT_KINDS:
                continue
            key_pattern = export["key"]
            value_pattern = export["value"]
            referenced = set(_PLACEHOLDER.findall(key_pattern + value_pattern))
            if kind == "env":
                secret_refs = referenced & secret_keys
                if secret_refs:
                    raise ValueError(f"env 导出不能引用敏感属性：{'、'.join(sorted(secret_refs))}")
            missing = referenced - set(all_attrs)
            if missing:
                continue
            key = _PLACEHOLDER.sub(lambda m: str(all_attrs.get(m.group(1), "")), key_pattern)
            value = _PLACEHOLDER.sub(lambda m: str(all_attrs.get(m.group(1), "")), value_pattern)
            if not key:
                raise ValueError(f"导出键为空：{key_pattern}")
            if "{" in key or "}" in key:
                raise ValueError(f"导出键包含未替换的占位符：{key}")
            if alias:
                key = alias + "_" + key
            exports[kind].append((key, value))
    return exports


def check_conflicts(bindings: List[Dict[str, Any]]) -> None:
    """检查多个绑定的导出是否冲突。bindings: [{alias, exports: {env: [(k, v)], secret_env: [...]}}]。"""
    seen_env: Dict[str, str] = {}
    seen_secret: Dict[str, str] = {}
    for binding in bindings:
        alias = binding.get("alias") or "无别名"
        for key, _ in binding["exports"].get("env") or []:
            if key in seen_env:
                raise ValueError(f"导出变量 {key} 冲突：绑定 {seen_env[key]} 和 {alias}")
            seen_env[key] = alias
        for key, _ in binding["exports"].get("secret_env") or []:
            if key in seen_secret:
                raise ValueError(f"敏感变量 {key} 冲突：绑定 {seen_secret[key]} 和 {alias}")
            seen_secret[key] = alias
