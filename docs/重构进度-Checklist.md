# XAC 架构迁移到 SWR - 完整 Checklist

## 阶段 1: 核心架构（已完成 ✅）
- [x] 领域模型（DeploymentUnit、Resource、ConfigLayer）
- [x] 统一参数解析器（ParamResolver，实现 XAC getParams）
- [x] 适配层（Store → 领域模型映射）
- [x] 资源推断引擎（从 K8s YAML 推断资源需求）
- [x] CLI 工具（analyze_yaml）

## 阶段 2: 数据库和资源管理（进行中 🔄）
- [ ] 数据库迁移 6：扩展资源表结构
- [ ] 资源类型定义完善（gauss_db/schema/user, redis, kafka, mqs, config_center, iam_endpoint, endpoint）
- [ ] 资源导出规则引擎（properties 文件解析）
- [ ] 资源绑定 CRUD（store/resources.py）
- [ ] 资源管理服务（services/resources.py）
- [ ] 资源管理 API（api/resources.py）

## 阶段 3: 配置解析集成（待完成 ⏳）
- [ ] 更新 delivery/resolve.py 使用 ParamResolver
- [ ] 配置快照包含资源版本
- [ ] 来源追踪存储到 deployment_manifests
- [ ] API 端点：GET /api/services/{id}/params
- [ ] 必填参数校验

## 阶段 4: 环境预置（待完成 ⏳）
- [ ] environment_presets 表
- [ ] 预置管理服务（services/presets.py）
- [ ] 预置管理 API（api/presets.py）
- [ ] 应用预置到环境（复制档案和配置集）

## 阶段 5: 导入增强（待完成 ⏳）
- [ ] 集成资源推断到导入流程
- [ ] 导入时自动创建推断的资源
- [ ] 导入报告显示资源绑定建议

## 阶段 6: 前端重构（待完成 ⏳）
- [ ] 参数来源追踪组件（ParamSourceViewer.vue）
- [ ] 资源选择器（ResourceSelector.vue）
- [ ] 资源列表页面
- [ ] 资源详情页面
- [ ] 资源绑定页面
- [ ] 服务配置页面集成来源追踪

## 阶段 7: 测试和文档（待完成 ⏳）
- [ ] 参数解析器单元测试
- [ ] 资源推断测试
- [ ] 端到端测试
- [ ] API 文档更新
- [ ] 用户手册更新
- [ ] 架构文档完善

---

## 当前进度：阶段 1 完成，开始阶段 2
