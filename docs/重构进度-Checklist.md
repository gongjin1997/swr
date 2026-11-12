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

## 阶段 3: 配置解析集成（进行中 🔄）
- [x] delivery/resolve.py 已集成资源导出
- [ ] 使用新的 ParamResolver 替换现有合并逻辑
- [ ] 配置快照包含资源版本
- [ ] 来源追踪存储到 deployment_manifests
- [ ] API 端点：GET /api/services/{id}/params（带来源追踪）
- [ ] 必填参数校验集成

## 阶段 4: 环境预置（待完成 ⏳）
- [ ] environment_presets 表结构验证
- [ ] 预置管理服务（services/presets.py）
- [ ] 预置管理 API（api/presets.py）
- [ ] 应用预置到环境（复制档案和配置集）
- [ ] 必填参数记录和校验

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

## 阶段 7: 测试和文档（待完成 ⏳）
- [ ] 参数解析器单元测试
- [ ] 资源推断测试
- [ ] 端到端测试
- [ ] API 文档更新
- [ ] 用户手册更新
- [ ] 架构文档完善

---

## 当前进度：阶段 2 完成，阶段 3 进行中（集成配置解析）

## 已提交：
- dbfca4a: 资源推断引擎、分析工具和完整 checklist
- ba0a687: 完整的资源管理系统（基于 XAC 架构）

