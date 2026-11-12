# XAC 架构迁移总结

本文档总结从 XAC 迁移到 SWR v2 的核心架构改进。

## 概述

XAC（eXtended Application Configuration）是一个应用配置管理系统，核心概念包括：
- **DU (Deployment Unit)**：部署单元，对应 SWR 的服务 (Service)
- **Resource**：资源抽象（数据库、缓存、消息队列等）
- **ConfigLayer**：配置层（feature、resource、config、du_attr、artifact、cert、external）
- **ParamResolver**：统一参数解析器，实现 6 层优先级和来源追踪

## 已迁移的核心能力

### 1. 领域模型（core/domain.py）

引入 XAC 的核心概念：
```python
@dataclass
class DeploymentUnit:
    id: int
    name: str
    config_layers: List[ConfigLayer]
    resource_bindings: List[ResourceBinding]
    attributes: Dict[str, Any]

@dataclass
class Resource:
    id: int
    type: str  # gauss_db, redis, kafka, mqs, etc.
    name: str
    scope_type: str  # platform, environment
    scope_id: int
    attributes: Dict[str, Any]
    version: int
    parent_chain: List[Dict[str, Any]]  # 支持层级资源
```

### 2. 统一参数解析器（core/param_resolver.py）

实现 XAC 的 `getParams()` 逻辑，6 层优先级：
```
feature(1) < resource(2) < config(平台<项目<环境<服务) < du_attr(3) < artifact(4) < cert(5) < external(6)
```

核心功能：
- 配置合并和覆盖检测
- 锁定参数保护
- 完整的来源追踪（source_layer、source_type、source_name、scope、version）
- 覆盖警告（哪一层被哪一层覆盖）

### 3. 资源类型系统（core/resource_types.py）

代码定义的资源类型（对应 XAC 的 ResourceType）：
- **gauss_db** / **gauss_schema** / **gauss_user**：支持层级关系
- **redis**：Redis 缓存
- **kafka**：Kafka 消息队列
- **mqs**：华为云消息队列
- **config_center**：配置中心
- **iam_endpoint**：IAM 认证端点
- **endpoint**：通用 HTTP 端点

每个资源类型包含：
- **AttributeDefinition**：属性定义（名称、类型、是否必填、默认值）
- **ExportRule**：导出规则（属性 → 配置的映射）

### 4. 资源导出引擎（core/resource_exporter.py）

将资源属性按导出规则转换为配置：
```python
export_rules = [
    ExportRule("env", "{host}", "spring.redis.host"),
    ExportRule("env", "{port}", "spring.redis.port"),
    ExportRule("secret_env", "{password}", "spring.redis.password"),
]
```

支持：
- 属性占位符替换（`{attr_name}`）
- 父子资源属性继承（gauss_db → schema → user）
- 敏感值掩码（预览时）

### 5. 资源推断引擎（core/resource_inference.py）

从 K8s YAML 自动推断资源需求：
```python
# 从 Deployment 的环境变量推断
env = [
    {"name": "REDIS_HOST", "value": "redis"},
    {"name": "KAFKA_BROKERS", "value": "kafka:9092"},
]
# 推断结果：redis (confidence=0.7), kafka (confidence=0.5)
```

推断规则（基于环境变量 key 模式）：
- `REDIS_*`, `spring.redis.*` → redis
- `KAFKA_BROKERS`, `spring.kafka.*` → kafka
- `spring.datasource.gauss.*`, `GAUSS_*` → gauss_db
- `erpmq.mqs.*` → mqs
- `configcenter_url` → config_center
- `iam.endpoint`, `service_*_url` → iam_endpoint

### 6. 适配层（core/adapter.py）

将 Store 的数据模型转换为领域模型：
```python
class DomainAdapter:
    def load_deployment_unit(self, service_id: int) -> DeploymentUnit:
        # 加载服务 → DU
        # 加载配置集 → ConfigLayer
        # 加载资源绑定 → ResourceBinding
        # 构建父链（支持层级资源）
```

### 7. 资源管理系统

完整的资源 CRUD 和绑定管理：
- **store/resource_store.py**：资源存储层（版本管理、父子关系）
- **services/resource_service.py**：资源管理服务（权限检查、加密）
- **api/resource_api.py**：资源管理 API

API 端点：
```
GET    /api/resources              # 列出资源
POST   /api/resources              # 创建资源
GET    /api/resources/{id}         # 获取详情
PUT    /api/resources/{id}         # 更新属性（创建新版本）
DELETE /api/resources/{id}         # 删除资源

GET    /api/resources/types        # 资源类型定义

GET    /api/services/{id}/resource-bindings     # 服务的资源绑定
POST   /api/services/{id}/resource-bindings     # 绑定资源
DELETE /api/services/{id}/resource-bindings/{binding_id}  # 解绑
GET    /api/services/{id}/resource-exports      # 导出为配置
```

### 8. 环境预置系统（services/preset_service.py）

基于 XAC 的应用预置（Application Preset）：
```python
preset = {
    "name": "标准 Java 微服务",
    "profile_template": {...},      # 环境档案模板
    "config_defaults": {...},       # 默认配置集
    "required_resources": [...],    # 必需的资源类型
    "required_params": [...],       # 必填参数列表
}
```

功能：
- 应用预置到环境（创建档案、配置集）
- 校验环境是否满足预置要求
- 返回缺失的资源和参数

### 9. 配置解析集成

将资源导出集成到配置解析流程：
- **delivery/resolve.py**：已集成资源导出（保留原有逻辑，兼容 v1）
- **API**: `GET /api/services/{id}/params`：返回带来源追踪的参数

返回格式：
```json
{
  "params": {
    "spring.redis.host": {
      "value": "redis.example.com",
      "source_layer": "resource",
      "source_type": "resource",
      "source_name": "my-redis",
      "scope": "environment",
      "version": 1,
      "locked": false,
      "overridden": false
    }
  },
  "sources": {...},
  "warnings": [...]
}
```

### 10. 导入增强

集成资源推断到导入流程：
- **delivery/importer.py**：调用 `analyze_namespace_yaml()`
- 返回推断的资源类型和置信度
- 前端可据此提示用户创建资源

## 架构对比

| 概念 | XAC | SWR v2 |
|------|-----|--------|
| 部署单元 | DU | Service |
| 资源 | Resource | Resource |
| 配置层 | ConfigLayer | ConfigLayer |
| 参数解析 | getParams() | ParamResolver.resolve() |
| 资源导出 | ResourceExporter | ResourceExporter |
| 应用预置 | Application Preset | Environment Preset |
| 资源推断 | - | ResourceInferenceEngine |

## 优先级规则

### XAC 的 6 层优先级（从低到高）
1. **feature**: 特性开关
2. **resource**: 绑定的资源导出
3. **config**: 配置集（平台 < 项目 < 环境 < 服务）
4. **du_attr**: DU 属性（副本数、资源限制等）
5. **artifact**: 档案固定值
6. **cert**: 证书
7. **external**: 外部注入（运行时）

### SWR v2 实现
- feature、resource、config、du_attr、artifact、cert 已实现
- external 层暂未实现（预留）

## 配置来源追踪

每个参数包含完整的来源信息：
```python
{
    "source_layer": "resource",          # 哪一层
    "source_type": "resource",           # 类型（config/resource/attribute）
    "source_id": 123,                    # 来源 ID
    "source_name": "my-redis",           # 来源名称
    "scope": "environment",              # 作用域
    "version": 1,                        # 版本号
    "locked": false,                     # 是否锁定
    "overridden": false,                 # 是否被覆盖
    "overridden_by": "du_attr",          # 被哪一层覆盖
}
```

## 锁定参数保护

锁定参数（`locked=true`）不被后续层覆盖：
```python
# 平台层锁定
{"DB_HOST": {"value": "prod.db", "locked": true}}

# 服务层尝试覆盖 → 失败，生成警告
{"DB_HOST": {"value": "test.db", "locked": false}}

# 结果：DB_HOST = "prod.db"（平台层的值）
```

## 资源层级关系

支持父子资源（对应 XAC 的 parent_type）：
```
gauss_db (数据库实例)
  └─ gauss_schema (Schema)
       └─ gauss_user (用户)
```

子资源继承父资源的属性：
```python
# gauss_db
attributes = {"host": "gaussdb.example.com", "port": 5432, "database": "mydb"}

# gauss_schema (继承 host、port、database，添加 schema_name)
attributes = {...parent, "schema_name": "public"}

# gauss_user (继承所有父属性，添加 username、password)
attributes = {...parent_chain, "username": "app", "password": "***"}
```

## 测试覆盖

- **tests/test_param_resolver.py**：参数解析器单元测试
  - 优先级顺序
  - 锁定参数保护
  - 资源导出
  - DU 属性覆盖
  - 来源追踪

- **tests/test_resource_inference.py**：资源推断单元测试
  - 从 Deployment 推断
  - 从 ConfigMap 推断
  - 置信度计算
  - 属性建议

## 迁移成果总结

✅ **已完成**：
- 核心领域模型（7 个数据类）
- 统一参数解析器（6 层优先级 + 来源追踪）
- 资源类型系统（8 种资源类型 + 导出规则）
- 资源管理系统（CRUD + 绑定 + 版本管理）
- 资源导出引擎（属性 → 配置转换）
- 资源推断引擎（K8s YAML → 资源类型）
- 环境预置系统（应用预置 + 校验）
- 配置解析集成（resolve + API）
- 导入增强（集成资源推断）
- 单元测试（参数解析 + 资源推断）

⏳ **待完成**（前端集成）：
- 参数来源追踪组件（ParamSourceViewer.vue）
- 资源选择器（ResourceSelector.vue）
- 资源列表和详情页面
- 资源绑定页面
- 服务配置页面集成来源追踪
- 导入流程集成资源创建

## 架构优势

1. **统一抽象**：资源、配置、DU 属性统一处理
2. **可追溯性**：每个参数都知道来自哪里（层、作用域、版本）
3. **灵活性**：通过优先级和锁定机制实现精细控制
4. **扩展性**：资源类型和导出规则代码定义，易于添加新类型
5. **自动化**：资源推断减少手工配置
6. **安全性**：敏感值加密存储，导出时掩码

## 参考

- XAC 设计文档：[内部链接]
- SWR v2 系统架构：`docs/系统架构-SWR-Image-Builder-v2.md`
- API 文档：`docs/API 文档.md`
