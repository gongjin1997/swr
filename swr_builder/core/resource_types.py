"""资源类型定义（基于 XAC 的资源模型）。

资源类型代码定义，不存储在数据库中。每个类型包含：
- code: 类型标识
- label: 显示名称
- parent_type: 父类型（支持层级关系，如 gauss_db → gauss_schema → gauss_user）
- attributes: 属性定义列表
- export_rules: 导出规则（如何转换为配置）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class AttributeDefinition:
    """属性定义"""
    name: str
    label: str
    type: str  # string, int, boolean, secret
    required: bool = False
    default: Any = None
    description: str = ""


@dataclass
class ExportRule:
    """导出规则：资源属性 → 配置"""
    target_kind: str  # env, secret_env, files, secret_files
    template: str  # 模板字符串，使用 {attr_name} 占位符
    key: str  # 配置 key


@dataclass
class ResourceType:
    """资源类型定义"""
    code: str
    label: str
    parent_type: Optional[str] = None
    attributes: List[AttributeDefinition] = None
    export_rules: List[ExportRule] = None

    def __post_init__(self):
        if self.attributes is None:
            self.attributes = []
        if self.export_rules is None:
            self.export_rules = []


# 资源类型注册表
RESOURCE_TYPES: Dict[str, ResourceType] = {}


def register_resource_type(resource_type: ResourceType) -> None:
    """注册资源类型"""
    RESOURCE_TYPES[resource_type.code] = resource_type


def get_resource_type(code: str) -> Optional[ResourceType]:
    """获取资源类型定义"""
    return RESOURCE_TYPES.get(code)


# ============================================================================
# 资源类型定义（对应 XAC 的各种资源）
# ============================================================================

# GaussDB / OpenGauss 数据库
register_resource_type(ResourceType(
    code="gauss_db",
    label="GaussDB 数据库",
    attributes=[
        AttributeDefinition("host", "主机地址", "string", required=True),
        AttributeDefinition("port", "端口", "int", required=True, default=5432),
        AttributeDefinition("database", "数据库名", "string", required=True),
        AttributeDefinition("admin_user", "管理员用户", "string", required=True),
        AttributeDefinition("admin_password", "管理员密码", "secret", required=True),
    ],
    export_rules=[
        ExportRule("env", "{host}", "spring.datasource.gauss.host"),
        ExportRule("env", "{port}", "spring.datasource.gauss.port"),
        ExportRule("env", "{database}", "spring.datasource.gauss.database"),
    ],
))

# GaussDB Schema
register_resource_type(ResourceType(
    code="gauss_schema",
    label="GaussDB Schema",
    parent_type="gauss_db",
    attributes=[
        AttributeDefinition("schema_name", "Schema 名称", "string", required=True),
        AttributeDefinition("owner", "所有者", "string"),
    ],
    export_rules=[
        ExportRule("env", "{schema_name}", "spring.datasource.gauss.schema"),
    ],
))

# GaussDB 用户
register_resource_type(ResourceType(
    code="gauss_user",
    label="GaussDB 用户",
    parent_type="gauss_schema",
    attributes=[
        AttributeDefinition("username", "用户名", "string", required=True),
        AttributeDefinition("password", "密码", "secret", required=True),
    ],
    export_rules=[
        ExportRule("env", "{username}", "spring.datasource.gauss.username"),
        ExportRule("secret_env", "{password}", "spring.datasource.gauss.password"),
    ],
))

# Redis
register_resource_type(ResourceType(
    code="redis",
    label="Redis",
    attributes=[
        AttributeDefinition("host", "主机地址", "string", required=True),
        AttributeDefinition("port", "端口", "int", required=True, default=6379),
        AttributeDefinition("password", "密码", "secret"),
        AttributeDefinition("database", "数据库编号", "int", default=0),
    ],
    export_rules=[
        ExportRule("env", "{host}", "spring.redis.host"),
        ExportRule("env", "{port}", "spring.redis.port"),
        ExportRule("env", "{database}", "spring.redis.database"),
        ExportRule("secret_env", "{password}", "spring.redis.password"),
    ],
))

# Kafka
register_resource_type(ResourceType(
    code="kafka",
    label="Kafka",
    attributes=[
        AttributeDefinition("brokers", "Broker 地址", "string", required=True, description="逗号分隔，如 host1:9092,host2:9092"),
        AttributeDefinition("username", "用户名", "string"),
        AttributeDefinition("password", "密码", "secret"),
        AttributeDefinition("security_protocol", "安全协议", "string", default="PLAINTEXT"),
    ],
    export_rules=[
        ExportRule("env", "{brokers}", "spring.kafka.bootstrap-servers"),
        ExportRule("env", "{security_protocol}", "spring.kafka.security.protocol"),
        ExportRule("secret_env", "{username}", "spring.kafka.properties.sasl.jaas.config.username"),
        ExportRule("secret_env", "{password}", "spring.kafka.properties.sasl.jaas.config.password"),
    ],
))

# MQS（华为云消息队列）
register_resource_type(ResourceType(
    code="mqs",
    label="MQS 消息队列",
    attributes=[
        AttributeDefinition("nameserver", "Nameserver 地址", "string", required=True),
        AttributeDefinition("access_key", "Access Key", "string", required=True),
        AttributeDefinition("secret_key", "Secret Key", "secret", required=True),
        AttributeDefinition("group_id", "Group ID", "string"),
    ],
    export_rules=[
        ExportRule("env", "{nameserver}", "erpmq.mqs.nameserver"),
        ExportRule("env", "{group_id}", "erpmq.mqs.groupId"),
        ExportRule("secret_env", "{access_key}", "erpmq.mqs.accesskey"),
        ExportRule("secret_env", "{secret_key}", "erpmq.mqs.secretkey"),
    ],
))

# 配置中心
register_resource_type(ResourceType(
    code="config_center",
    label="配置中心",
    attributes=[
        AttributeDefinition("url", "配置中心地址", "string", required=True),
        AttributeDefinition("namespace", "命名空间", "string"),
        AttributeDefinition("username", "用户名", "string"),
        AttributeDefinition("password", "密码", "secret"),
    ],
    export_rules=[
        ExportRule("env", "{url}", "configcenter_url"),
        ExportRule("env", "{namespace}", "configcenter.namespace"),
        ExportRule("secret_env", "{username}", "configcenter.username"),
        ExportRule("secret_env", "{password}", "configcenter.password"),
    ],
))

# IAM 端点（认证/授权服务）
register_resource_type(ResourceType(
    code="iam_endpoint",
    label="IAM 端点",
    attributes=[
        AttributeDefinition("endpoint", "IAM 端点地址", "string", required=True),
        AttributeDefinition("account", "账号", "string", required=True),
        AttributeDefinition("secret", "密钥", "secret", required=True),
    ],
    export_rules=[
        ExportRule("env", "{endpoint}", "iam.endpoint"),
        ExportRule("env", "{account}", "iam.account"),
        ExportRule("secret_env", "{secret}", "iam.secret"),
    ],
))

# 通用端点（其他 HTTP 服务）
register_resource_type(ResourceType(
    code="endpoint",
    label="HTTP 端点",
    attributes=[
        AttributeDefinition("url", "URL", "string", required=True),
        AttributeDefinition("api_key", "API Key", "secret"),
        AttributeDefinition("timeout", "超时时间（秒）", "int", default=30),
    ],
    export_rules=[
        ExportRule("env", "{url}", "external.service.url"),
        ExportRule("secret_env", "{api_key}", "external.service.api_key"),
        ExportRule("env", "{timeout}", "external.service.timeout"),
    ],
))
