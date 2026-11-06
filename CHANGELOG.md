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
