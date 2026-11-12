# SWR Image Builder v2 全量重构方案 - 基于 XAC 架构

## 1. 当前问题分析

### 1.1 现有架构的局限性
- **补丁式开发**：在现有 v1.3 架构上打补丁，导致概念不统一、层次不清晰
- **缺少核心抽象**：没有统一的资源模型、配置管理和部署单元抽象
- **耦合度高**：delivery、services、store 层职责混乱，难以扩展
- **前端割裂**：页面按功能零散分布，缺少统一的应用/环境/资源视图

### 1.2 XAC 的核心优势
- **清晰的领域模型**：DU（部署单元）、Resource（资源）、AppConfig（应用配置）、Artifact（制品）
- **参数分层机制**：feature < resource < DU attr < artifact placeholders < cert，优先级明确
- **模块化设计**：platform-config、platform-artifact、platform-deploy 职责分离
- **统一的配置视图**：getParams 提供完整的参数来源追踪和合并逻辑

## 2. 全量重构目标

### 2.1 核心领域模型重建
```
应用 (Application)
  └─ 部署单元 (DeploymentUnit / DU)
       ├─ 制品 (Artifact)：镜像、Helm Chart、配置包
       ├─ 资源绑定 (ResourceBinding)：DB、Redis、Kafka 等
       ├─ 应用配置 (AppConfig)：分层参数
       └─ 部署目标 (DeployTarget)：环境 + 命名空间

资源 (Resource)
  ├─ 类型定义 (ResourceType)
  ├─ 属性 (Attributes)：普通 + secret
  ├─ 父子关系 (Parent-Child)：gauss_db → schema → user
  └─ 导出规则 (ExportRules)：如何注入到配置

环境 (Environment)
  ├─ 集群 (Cluster)
  ├─ 命名空间 (Namespace)
  ├─ 环境档案 (Profile)
  └─ 预置模板 (Preset)

配置 (Configuration)
  ├─ 配置集 (ConfigSet)：platform / project / environment / du
  ├─ 参数来源 (ParamSource)：resource / config / preset / artifact
  └─ 合并规则 (MergeRules)：优先级 + 锁定
```

### 2.2 模块重组
```
swr-builder/
├── core/                          # 核心领域模型
│   ├── domain/
│   │   ├── application.py         # 应用和 DU
│   │   ├── resource.py            # 资源模型
│   │   ├── environment.py         # 环境模型
│   │   ├── configuration.py       # 配置模型
│   │   └── artifact.py            # 制品模型
│   ├── param_resolver.py          # 参数解析器（对应 XAC getParams）
│   └── resource_types.py          # 资源类型定义
│
├── modules/                       # 业务模块
│   ├── application/               # 应用管理模块
│   │   ├── service.py
│   │   ├── repository.py
│   │   └── api.py
│   ├── resource/                  # 资源管理模块
│   │   ├── service.py
│   │   ├── repository.py
│   │   └── api.py
│   ├── environment/               # 环境管理模块
│   │   ├── service.py
│   │   ├── repository.py
│   │   └── api.py
│   ├── configuration/             # 配置管理模块
│   │   ├── service.py
│   │   ├── repository.py
│   │   └── api.py
│   └── deployment/                # 部署模块
│       ├── renderer.py            # K8s 对象渲染
│       ├── applier.py             # Server-Side Apply
│       ├── worker.py              # 部署 worker
│       └── api.py
│
├── infrastructure/                # 基础设施
│   ├── database/
│   │   ├── schema.py
│   │   ├── migrations.py
│   │   └── connection.py
│   ├── kubernetes/
│   │   └── client.py
│   └── security/
│       └── crypto.py
│
└── web/                           # 前端（全量重构）
    └── src/
        ├── layouts/
        │   ├── AppLayout.vue      # 应用视图布局
        │   ├── EnvLayout.vue      # 环境视图布局
        │   └── ResourceLayout.vue # 资源视图布局
        ├── views/
        │   ├── application/       # 应用管理
        │   │   ├── AppList.vue
        │   │   ├── AppDetail.vue
        │   │   ├── DUConfig.vue   # DU 配置页
        │   │   └── DUDeploy.vue   # DU 部署页
        │   ├── resource/          # 资源管理
        │   │   ├── ResourceList.vue
        │   │   ├── ResourceDetail.vue
        │   │   └── ResourceBinding.vue
        │   ├── environment/       # 环境管理
        │   │   ├── EnvList.vue
        │   │   ├── EnvDetail.vue
        │   │   └── PresetManage.vue
        │   └── deployment/        # 部署管理
        │       ├── DeployList.vue
        │       ├── DeployDetail.vue
        │       └── DeployApproval.vue
        └── components/
            ├── ParamSourceViewer.vue  # 参数来源追踪组件
            ├── ResourceSelector.vue   # 资源选择器
            └── ConfigEditor.vue       # 配置编辑器
```

## 3. 数据模型重建

### 3.1 核心表结构
```sql
-- 应用和部署单元
CREATE TABLE applications (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL,
    name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE(project_id, name)
);

CREATE TABLE deployment_units (
    id BIGSERIAL PRIMARY KEY,
    application_id BIGINT NOT NULL REFERENCES applications(id),
    name TEXT NOT NULL,
    type TEXT NOT NULL,  -- k8s-deployment, helm, lambda
    spec JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE(application_id, name)
);

-- 资源模型
CREATE TABLE resource_types (
    code TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    parent_type TEXT REFERENCES resource_types(code),
    attributes JSONB NOT NULL,
    export_rules JSONB NOT NULL
);

CREATE TABLE resources (
    id BIGSERIAL PRIMARY KEY,
    type_code TEXT NOT NULL REFERENCES resource_types(code),
    scope_type TEXT NOT NULL,  -- platform, environment
    scope_id BIGINT NOT NULL,
    name TEXT NOT NULL,
    parent_id BIGINT REFERENCES resources(id),
    current_version_id BIGINT,
    UNIQUE(scope_type, scope_id, name)
);

CREATE TABLE resource_versions (
    id BIGSERIAL PRIMARY KEY,
    resource_id BIGINT NOT NULL REFERENCES resources(id),
    version INTEGER NOT NULL,
    attributes JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE(resource_id, version)
);

-- DU 资源绑定
CREATE TABLE du_resource_bindings (
    id BIGSERIAL PRIMARY KEY,
    du_id BIGINT NOT NULL REFERENCES deployment_units(id),
    resource_id BIGINT NOT NULL REFERENCES resources(id),
    alias TEXT,
    UNIQUE(du_id, resource_id)
);

-- 配置模型
CREATE TABLE app_configs (
    id BIGSERIAL PRIMARY KEY,
    scope_type TEXT NOT NULL,  -- platform, project, environment, du
    scope_id BIGINT NOT NULL,
    type TEXT NOT NULL,  -- global, du-global, default, du-default, env, secret_env
    entries JSONB NOT NULL,
    version INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

-- 部署记录
CREATE TABLE deploy_records (
    id BIGSERIAL PRIMARY KEY,
    du_id BIGINT NOT NULL,
    environment_id BIGINT NOT NULL,
    artifact_id BIGINT NOT NULL,
    param_snapshot JSONB NOT NULL,  -- 参数快照
    resource_versions JSONB NOT NULL,  -- 资源版本钉住
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
```

### 3.2 资源类型改为数据驱动
- 不在代码中硬编码资源类型，改为存储在 `resource_types` 表
- 支持用户自定义资源类型（高级功能）
- 导出规则使用 JSON 配置，支持复杂的转换逻辑

## 4. 参数解析器重建

### 4.1 参数来源优先级（参考 XAC getParams）
```python
class ParamResolver:
    """
    参数解析器：合并多层配置，返回最终参数和来源追踪
    
    优先级（从低到高）：
    1. feature 层（平台特性默认值）
    2. resource 层（绑定的资源导出）
    3. DU attributes（DU 自身属性）
    4. artifact placeholders（制品中的占位符）
    5. certificate（证书引用）
    6. externalParams（外部传入，最高优先级）
    
    特殊规则：
    - 锁定的 key 不能被下层覆盖
    - 必填参数缺失时报错
    """
    
    def resolve(self, du: DeploymentUnit, environment: Environment, 
                artifact: Artifact, external_params: Dict = None) -> ResolvedParams:
        """
        解析参数，返回：
        - params: 最终参数字典
        - sources: 每个 key 的来源追踪
        - warnings: 警告列表（被覆盖、被锁定等）
        - errors: 错误列表（必填参数缺失）
        """
        pass
```

### 4.2 来源追踪结构
```python
@dataclass
class ParamSource:
    key: str
    value: Any
    source_type: str  # resource, config, artifact, external
    source_id: int
    source_name: str
    layer: str  # platform, project, environment, du
    version: int
    locked: bool
    overridden: bool
    override_by: Optional[str]  # 被哪一层覆盖
```

## 5. 前端重构方案

### 5.1 三大主视图
```
1. 应用视图（Application View）
   - 应用列表 → 应用详情 → DU 列表 → DU 配置 → DU 部署历史
   - 核心流程：创建应用 → 添加 DU → 配置参数 → 绑定资源 → 部署
   
2. 环境视图（Environment View）
   - 环境列表 → 环境详情 → 命名空间 → 运行的 DU → 资源
   - 核心流程：创建环境 → 应用预置 → 纳管命名空间 → 部署 DU
   
3. 资源视图（Resource View）
   - 资源列表（按类型分组）→ 资源详情 → 使用情况 → 版本历史
   - 核心流程：创建资源 → 配置属性 → 查看绑定 → 版本管理
```

### 5.2 统一的配置编辑器
```vue
<template>
  <ConfigEditor 
    :du="currentDU"
    :environment="currentEnv"
    :show-source-trace="true"
    @save="handleSave"
  >
    <!-- 左侧：参数编辑 -->
    <template #editor>
      <el-form>
        <!-- 环境变量 -->
        <!-- 敏感变量 -->
        <!-- 配置文件 -->
      </el-form>
    </template>
    
    <!-- 右侧：来源追踪面板 -->
    <template #source-trace>
      <ParamSourceViewer :sources="paramSources" />
    </template>
  </ConfigEditor>
</template>
```

## 6. 实施路径

### 6.1 阶段 1：核心领域模型（2 周）
- [ ] 数据库 schema 重建（基于 XAC 的表结构）
- [ ] 核心领域对象：Application、DU、Resource、Environment
- [ ] ParamResolver 实现
- [ ] 迁移脚本（从现有 v2 数据迁移）

### 6.2 阶段 2：后端模块重构（3 周）
- [ ] 应用管理模块（application/）
- [ ] 资源管理模块（resource/）
- [ ] 环境管理模块（environment/）
- [ ] 配置管理模块（configuration/）
- [ ] 部署模块（deployment/）
- [ ] API 重新设计（RESTful，统一响应格式）

### 6.3 阶段 3：前端全量重构（3 周）
- [ ] 布局系统重建（AppLayout、EnvLayout、ResourceLayout）
- [ ] 应用视图（应用列表、DU 配置、部署流程）
- [ ] 环境视图（环境列表、预置应用、DU 管理）
- [ ] 资源视图（资源列表、绑定管理、版本历史）
- [ ] 通用组件（ParamSourceViewer、ResourceSelector、ConfigEditor）

### 6.4 阶段 4：迁移和验证（1 周）
- [ ] 数据迁移脚本测试
- [ ] 端到端测试（创建应用 → 部署 → 运维）
- [ ] 性能测试
- [ ] 文档更新

## 7. 风险和缓解

### 7.1 主要风险
- **时间成本高**：全量重构预计 8-10 周
- **数据迁移复杂**：现有数据需要映射到新模型
- **功能回归风险**：可能遗漏现有功能

### 7.2 缓解措施
- 分阶段交付，每个阶段独立验证
- 保留现有 v2 分支，新重构在 feature/v3-xac-refactor 分支
- 编写完整的数据迁移脚本和回滚方案
- 功能清单对比（v2 vs v3）

## 8. 决策点

需要用户确认：
1. **是否接受 8-10 周的重构周期？**
2. **是否采用 XAC 的模块划分（platform-config、platform-artifact、platform-deploy）？**
3. **资源类型是否改为数据驱动（存储在数据库）还是继续代码定义？**
4. **前端是否完全重写还是保留部分现有页面？**
5. **是否需要兼容现有 v2 的数据和 API？**

---

## 附录：XAC vs SWR 概念映射

| XAC 概念 | SWR v2 概念 | 重构后概念 |
|---------|------------|----------|
| DU（部署单元） | Service | DeploymentUnit |
| Resource | （缺失） | Resource |
| DU_RESOURCE_T | （缺失） | DUResourceBinding |
| APP_CONFIG_T | ConfigSet | AppConfig |
| getParams | resolve.py | ParamResolver |
| 哨兵环境 -1/0 | （缺失） | EnvironmentPreset |
| ARTIFACT_T | Job (构建) | Artifact |
| 制品类型 25 种 | 只有镜像 | 扩展支持 Helm、配置包 |
