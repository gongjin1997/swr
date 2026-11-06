# 华为云 Stack 8.5.1 后端接口接入设计

版本：v1.1.1。

## 1. 本次实现与现场验收边界

**已实现 AK/SK 签名调用 `POST /v3/auth/tokens`、读取 `X-Subject-Token`、Token 有效期与作用域校验、并发合并、缓存及只读请求遇到 401 后最多刷新一次。** HCS 认证默认模式为 `aksk`；密码模式与预置 Token 需显式选择，失败不会静默回退。

实现是真实 HTTP 客户端和官方 SDK 签名调用，不是返回固定 Token 的占位代码。53 项自动化测试已通过，其中新增签名对照及模拟 IAM 服务验证。

**尚未连接用户的真实 Stack 环境。** 测试验证客户端代码和签名行为，不等于现场网关已经验收。不同网关可能要求不同请求体或签名版本，当前配置和边界如下：

- URL：显式配置 IAM Endpoint，再追加 `/v3/auth/tokens`；不从 SWR 主机名猜地址。
- 签名：使用 `huaweicloudsdkcore==3.1.219` 官方 `Signer`，算法为 `SDK-HMAC-SHA256`，Authorization 为官方当前 SDK 的 `Access=...` 格式。
- 作用域：项目模式使用 `X-Project-Id`；账号模式使用 `X-Domain-Id`，签名覆盖这些头。必须配置对应 ID，不能拿名称代替。
- 默认请求体为零字节空体，不编造 `identity.methods`；适用于由签名请求头确定身份和作用域的网关配置。
- 如现场网关要求 JSON，管理员需显式填写 `aksk_request_body`；平台对最终序列化后、实际发送的 UTF-8 字节签名。不会失败后轮流盲试其他身份方法或请求体。
- HCS IAM 通用头文档中的某些示例展示旧式 `Credential=...`；本版没有实现旧式 Credential 签名或假定它与 Access 可互换。如果现场仅接受该旧式格式，应按现场官方兼容 SDK/算法增加专用策略，不能直接改 Authorization 字符串。

## 2. 依据与证据范围

- [HCS IAM 2.0 8.5.1 通用请求头](https://support.huawei.com/enterprise/en/doc/EDOC1100477481/18e2a232/common-request-headers)：确认 AK/SK 所需时间、授权、Host 请求头，以及项目/账号 Token 作用域头。
- [华为官方 Python 签名指南](https://support.huaweicloud.com/intl/en-us/devg-apisign/api-sign-sdk-python.html)：确认使用签名 SDK，AK/SK 由环境变量或加密配置提供，不写入源码。
- [华为官方 SDK 签名源码](https://github.com/huaweicloud/huaweicloud-sdk-python-v3/blob/master/huaweicloud-sdk-core/huaweicloudsdkcore/signer/signer.py)：本实现调用官方签名组件，不自行推导 SK 签名算法。
- [华为官方签名示例 SDK 2.0.7](https://obs.cn-north-1.myhuaweicloud.com/apig-sdk/ApiGateway-python-sdk.zip)：用于生成固定签名对照向量，测试数据仅使用虚拟凭证。
- [CCE 25.3.0-HCS for HCS 8.5.1：Making an API Request](https://support.huawei.com/enterprise/en/doc/EDOC1100453109/9b1290bc/making-an-api-request)：确认 Token 端点和响应头、Endpoint 需从实际环境获取。此页的密码请求体不能直接当作 AK/SK 请求体。

以上依据不等于已取得用户现场网关的完整 AK/SK 换 Token 请求样例；现场请求体与签名兼容性仍需确认。

## 3. 认证流程

```text
管理员配置 AK/SK、IAM Endpoint、项目/账号 ID
  → 检查 TLS、凭证与作用域
  → 形成最终请求体字节（默认空体，或显式 JSON 模板）
  → 加入 Host / Content-Type / Content-Length / X-Sdk-Date / 作用域头
  → 临时 AK/SK 可加入 X-Security-Token
  → 官方 Signer 计算 Authorization
  → POST /v3/auth/tokens（禁止自动跟随重定向）
  → 必须收到 200/201 和 X-Subject-Token、有效 token JSON
  → 校验 expires_at 及返回的项目/账号 ID
  → 内存缓存，提前 60 秒失效；缺失有效期则不复用缓存
  → 后续服务请求使用 X-Auth-Token
```

同一 HCSClient 的并发认证由锁合并；只读 GET 遇到 401 最多重新换 Token 并重试一次。不会无限重试，不自动重放资源写操作。

SK 只进入本地签名器，不作为密码、请求体或 HTTP 请求头发送。日志、认证 API 和 CLI 不返回原始 Token、AK/SK、Authorization 或云端原始错误正文。

## 4. 最小配置与页面操作

```yaml
hcs:
  enabled: false                 # 首次测试可关闭；测试按钮/诊断命令为显式操作
  version: "8.5.1"
  iam_endpoint: "https://<实际 IAM Endpoint>:<端口>"
  auth_mode: aksk
  token_scope: project           # 或 domain
  project_id: "<项目 ID>"
  domain_id: ""                 # 账号级 Token 时填写
  ak: ""                        # 推荐 HCS_AK 环境变量
  sk: ""                        # 推荐 HCS_SK 环境变量
  security_token: ""            # 临时凭证才需要
  aksk_request_body: ""          # 默认空体；仅按现场网关契约填写 JSON 模板
  ca_file: "/path/to/hcs-ca.pem"
  insecure: false
  timeout: 30
```

页面：系统设置 → 华为云 Stack 后端 → AK/SK 签名换 Token → 填入专用凭证及作用域 → “测试 IAM 认证” → 成功后保存。测试候选表单不会自动保存。

环境变量：`HCS_IAM_ENDPOINT`、`HCS_AK`、`HCS_SK`、`HCS_SECURITY_TOKEN`、`HCS_PROJECT_ID`、`HCS_DOMAIN_ID`。环境值优先、页面只读；数据库中的秘密值加密保存。

命令行诊断：

```bash
python -m swr_builder.cloud -c config.yaml
```

输出仅含结果、认证方式、作用域及过期时间，不包含 Token。

### JSON 模板配置

仅在现场网关明确要求 body 时使用，不代表所有 HCS IAM 都采用该 body。模板支持值为完整字符串的 `${ak}`、`${project_id}`、`${domain_id}`，先解析 JSON，再替换对象值、统一序列化并签名；不进行不安全的字符串拼接。

例如现场要求仅携带 scope 对象时可配置：

```yaml
hcs:
  aksk_request_body: '{"auth":{"scope":{"project":{"id":"${project_id}"}}}}'
```

此示例只是可配置请求格式，不是已确认的所有现场通用契约。不支持 `${sk}`，拒绝 SK/密码字段及秘密值进入请求体。

## 5. 三类凭证隔离

| 凭证域 | 用途 | 配置位置 |
| --- | --- | --- |
| SWR 长期登录密钥 / SWR AK-SK 推导登录密钥 | docker login、Registry V2 | 镜像仓库设置 |
| HCS AK/SK，及换取的 IAM Token | HCS 云服务管理 API | HCS 后端设置 |
| Kubernetes ServiceAccount Token | 集群原生 Deployment API | 部署目标 |

平台账号不与云账号共享。不能将 SWR 登录密钥当作 IAM Token，也不能假设 IAM X-Auth-Token 可直接作为 Kubernetes Bearer Token 使用。

## 6. 能力矩阵

| 能力 | 状态 | 现场依赖 |
| --- | --- | --- |
| SWR Registry V2 构建与推送 | 已实现 | 网络、CA、Docker、组织权限 |
| AK/SK 签名换 IAM Token | 已实现客户端及模拟测试，默认认证模式 | Endpoint、网关签名兼容性、作用域、请求体契约、时间同步 |
| 临时 AK/SK + Security Token | 已实现 | 有效临时凭证和网关支持 |
| IAM 密码认证 / 预置 Token | 可选模式 | 对应策略；预置 Token 测试只装载、不会声称已验证 |
| HCS 内部只读客户端 | 已实现 | 具体服务路径需按现场文档登记 |
| Kubernetes Deployment 更新 | 预览实现 | 现有工作负载、RBAC、imagePullSecrets |
| SWR/CCE/ECS 管理面业务 API | Endpoint 与服务接口预留 | 实际组件版本、路径、分页、限流和权限 |
| ManageOne SSO、SSH、审批、自动回滚 | 未实现 | 独立需求与安全设计 |

## 7. 现场故障排查

| 现象 | 核查 |
| --- | --- |
| TLS / 网络失败 | 构建机路由、防火墙、代理、实际 Endpoint、CA 信任链 |
| 301/302/307 等重定向 | 当前主动拒绝转发凭证，改用最终 IAM 服务地址，不用登录门户 |
| 401 / 403 | AK 是否属于目标项目/账号、SK 是否正确、是否需临时 Security Token、签名版本、UTC/NTP、权限 |
| 400 / 不接受空请求体 | 核实网关准确请求体，显式配置 JSON 模板；不要回退密码或盲试 identity 方法 |
| 缺少 X-Subject-Token / token 对象 | 检查是否误连 ManageOne 页面、代理替换响应、接口版本 |
| 返回作用域缺失或不同 | 主动拒绝，检查 project/domain 类型及 ID，不使用不匹配 Token |
| 返回 Token 已过期 | 核查时间同步、云端时间及凭证状态 |
| 只读服务仍然 401 | 最多刷新一次后报错，继续核实对应服务 Endpoint、Token 作用域和权限 |

## 8. 后续接入路线

1. 使用专用最小权限 AK/SK，先在真实环境完成认证验收，不在聊天发送凭证。
2. 按实际 SWR、CCE/CCE 敏捷版、Kubernetes 组件版本登记只读服务 API；HCS 平台版本不能替代组件版本确认。
3. 测试 namespace 先验证现有 Deployment 发布、架构、镜像拉取、探针、滚动策略。
4. 生产发布审批、灰度、自动回滚、资源创建和配额管理按独立需求实现，不将构建账号扩成云平台管理员。
