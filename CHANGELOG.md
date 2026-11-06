# v1.3.0-dev.1

**前端：Vue 3 重写**
- 删除原生 JS 页面（`swr_builder/static/`），新前端工程在 `web/`：Vue 3 + TypeScript + Vite + Vue Router + Pinia + Element Plus。
- history 路由：刷新保留当前页面，支持深链接（如 `/resources/services/3`、`/jobs?detail=12`），登录后回到原地址。
- 登录、强制改密、权限检查统一在路由守卫中处理；导航菜单由同一份路由表按权限生成。
- 统一 API 客户端：401 跳登录、`PASSWORD_CHANGE_REQUIRED` 跳改密页、错误提示统一；服务发布不再绕过统一处理。
- 项目 SWR 授权、命名空间发布窗口、服务配置改为表单编辑；服务详情页支持修订对比、回滚、重新发布、密钥设置。
- 构建日志与发布日志共用组件，离开页面自动停止轮询；跟随系统深色模式。
- 构建产物由后端托管：`/assets/*` 长期缓存，其他页面路径回落到 `index.html`；Dockerfile 改为多阶段构建。

**后端：分层重构（API 路由表与 v1.2 完全一致）**
- `main.py`/`routes.py` 拆为 `api/`（路由）、`services/`（业务规则）、`web/`（权限表、中间件、前端托管）；`main.py` 保留兼容导出。
- `store.py` 拆为 `store/` mixin 包，迁移改为注册表 + 统一执行器（版本号与校验和不变）。
- 错误响应增加机器可读的 `code` 字段，`detail` 文案不变；服务接口提示统一为简体中文。
- 新增 CSP 响应头；`secure_cookie` 时加 HSTS；`/docs`、`/openapi.json` 默认关闭（`server.api_docs: true` 开启）。
- 处理函数已记录业务审计的请求，中间件不再重复记录通用审计。

**缺陷修复**
- 构建：同一仓库排队的任务不再占用全局并发名额；仓库锁用完即回收。
- 系统设置：保存失败时内存和数据库都恢复到修改前，回滚中的回调异常不再覆盖原始错误。
- 服务发布 worker：单次异常不再导致 worker 退出；租约改为 300 秒并每 60 秒续租，过期的执行中任务标记为“结果待确认”并释放发布槽；缺少新字段的旧命名空间策略按默认值处理；审批人数只由一处规则计算。
- `get_project` / `get_namespace` 改为按主键查询。
- 删除旧部署目标的执行代码（接口自 v1.2 起已返回 410）。

# v1.2.0-dev.2

- 新增校验和 SQLite 迁移、默认项目回填、项目和命名空间 scoped role bindings、项目/namespace 行级授权。
- 未登记的 API 路由默认拒绝；构建仓库和 Dockerfile 基础镜像按项目 SWR 前缀授权。
- 部署目标必须显式绑定应用；新服务部署固定 SHA-256 digest。
- 新增集群 SA 凭据加密、namespace 纳管、策略校验、SSAR 自检和固定 RBAC YAML 预览。
- 新增 image-only 服务、不可变 revision、CAS 更新、独立加密 Secret 版本、发布审批与幂等部署请求。
- 新增 SQLite 服务部署队列、lease/fencing 检查、unknown 中断状态及 Kubernetes rollout 校验。
- Namespace 冻结截止时间与按 IANA 时区配置的发布窗口在入队及 Kubernetes 写入前生效；worker 会在外部写入前复核授权和审批条件。
- 资源页支持编辑 namespace 审批、冻结截止时间和按星期/时区配置的发布窗口。
- 前端新增项目、集群、命名空间、服务与审批工作区。
- 限制：managed SSA、CCE 证书生命周期、用户组/SSO 和真实云端验收仍未完成。

# v1.1.1 交付说明

## AK/SK 换 Token 已实现

- HCS 默认认证改为 aksk；官方 huaweicloudsdkcore 签名 POST /v3/auth/tokens。
- 显式项目/账号作用域、临时凭证、JSON 模板、Token 缓存及刷新、安全错误。
- 页面支持模式选择、专用凭证、候选认证测试；CLI 诊断不输出 Token。
- 53 项自动化测试通过，包含独立官方签名向量及模拟 IAM 服务验证。
- 尚未连接用户真实环境；默认空体与 SDK Access 签名兼容性需现场验收，旧式 Credential 签名未实现。

## 保留 v1.1.0 新增能力

- 独立登录、记住我、首次改密、失败锁定。
- 系统设置、凭证加密、环境变量锁定。
- 账号/角色、个人资料、会话、API 令牌、操作审计。
- Webhook / Kubernetes 部署预览、目标/记录/日志、事件总线；SSH、审批、自动回滚预留。
- 完整设计、开发、HCS 接入及测试文档。

首次 admin 初始化、升级备份与生产 HTTPS 要求见 README。SWR 的 AK/SK 登录密钥推导不是 HCS 管理 API 签名，不能混用。
