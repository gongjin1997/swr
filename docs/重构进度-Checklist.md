# XAC 架构迁移到 SWR - 完整 Checklist

## 阶段 1: 核心架构（已完成 ✅）
- [x] 领域模型（DeploymentUnit、Resource、ConfigLayer）
- [x] 统一参数解析器（ParamResolver，实现 XAC getParams）
- [x] 适配层（Store → 领域模型映射）
- [x] 资源推断引擎（从 K8s YAML 推断资源需求）
- [x] CLI 工具（analyze_yaml）

## 阶段 2: 资源管理（已完成 ✅）
- [x] 资源类型定义（8 种资源类型，代码定义）
- [x] 资源导出引擎（属性 → 配置转换）
- [x] 资源存储层（resource_store.py）
- [x] 资源管理服务（resource_service.py）
- [x] 资源管理 API（resource_api.py）
- [x] 服务资源绑定 API

## 阶段 3: 配置解析集成（已完成 ✅）
- [x] delivery/resolve.py 已集成资源导出
- [x] 使用新的 ParamResolver 替换现有合并逻辑
- [x] 配置快照包含资源版本
- [x] 来源追踪存储到 deployment_manifests
- [x] API 端点：GET /api/services/{id}/params（带来源追踪）
- [x] 必填参数校验集成

## 阶段 4: 环境预置（已完成 ✅）
- [x] environment_presets 表结构已存在
- [x] 预置管理服务（services/preset_service.py）
- [x] 预置管理 API（api/preset_api.py）
- [x] 应用预置到环境（复制档案和配置集）
- [x] 必填参数记录和校验

## 阶段 5: 导入增强（已完成 ✅）
- [x] 集成资源推断到导入流程
- [x] 导入分析返回推断的资源类型
- [ ] 导入确认时自动创建推断的资源（前端集成）

## 阶段 6: 前端重构（待完成 ⏳）
- [ ] 参数来源追踪组件（ParamSourceViewer.vue）
- [ ] 资源选择器（ResourceSelector.vue）
- [ ] 资源列表页面
- [ ] 资源详情页面
- [ ] 资源绑定页面
- [ ] 服务配置页面集成来源追踪

## 阶段 7: 测试和文档（已完成 ✅）
- [x] 参数解析器单元测试
- [x] 资源推断测试
- [ ] 端到端测试（需要前端完成）
- [x] 架构文档完善（XAC 迁移总结）
- [ ] API 文档更新（待前端完成后统一更新）
- [ ] 用户手册更新（待前端完成后统一更新）

---

## 当前进度：后端重构完成（阶段 1-5, 7 部分完成），前端待集成

## 已提交：
- 3f2c10d: 核心领域模型和统一参数解析器
- dbfca4a: 资源推断引擎、分析工具和完整 checklist
- ba0a687: 完整的资源管理系统（基于 XAC 架构）
- 4561152: 集成资源导出到参数解析器
- 0db5e3b: API 端点返回带来源追踪的参数
- 893a100: 环境预置管理系统（基于 XAC 应用预置）
- 2695a5f: 集成资源推断到导入流程

## 核心成果：
- 7 个领域模型类
- 6 层配置优先级（feature < resource < config < du_attr < artifact < cert < external）
- 8 种资源类型（gauss_db/schema/user, redis, kafka, mqs, config_center, iam_endpoint, endpoint）
- 完整的来源追踪（source_layer, source_type, source_name, scope, version, locked, overridden）
- 资源推断引擎（从 K8s YAML 自动推断资源需求）
- 环境预置系统（应用预置 + 校验）
- 单元测试覆盖（参数解析 + 资源推断）
- 架构文档（XAC 迁移总结）

## 前端集成要点：
1. 参数来源追踪：调用 GET /api/services/{id}/params，显示每个参数的来源、作用域、版本
2. 资源管理：调用 /api/resources 接口，CRUD 资源
3. 资源绑定：在服务配置页面添加资源绑定选择器
4. 导入增强：在导入确认页面显示推断的资源，允许用户一键创建并绑定
5. 环境预置：在环境管理页面添加预置应用功能


