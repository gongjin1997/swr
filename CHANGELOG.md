# v2.0.0-dev.1

在 v1.3 的基础上补上“新增服务”：数据库换成 PostgreSQL / GaussDB，环境配置分层维护，平台按模板生成并下发服务的全部对象，已有服务可以导入后由平台接管。设计与取舍见 [docs/v2-服务创建与环境配置设计.md](docs/v2-服务创建与环境配置设计.md)。

**升级须知（不兼容）**
- 运行数据库改为 PostgreSQL 14+，或以 `DBCOMPATIBILITY 'PG'` 创建的 GaussDB / openGauss。需要配置 `database.url`（或 `BUILDER_DATABASE_URL`）与 `BUILDER_DATABASE_PASSWORD`。
- v1.x 的 SQLite 数据用 `python -m swr_builder db import-sqlite --from data/builder.db` 导入空库，必须使用原来的 `secret.key`。
- 离线安装包还没有改为 PostgreSQL，v2 暂时只能用源码或容器方式部署。
- 命名空间的 RBAC 模板增加了 Secret、ReplicaSet、事件的规则，策略开启 Ingress / HPA 时还有对应规则。已纳管的命名空间要按新模板重新授权后才能使用托管服务。
- `GET /api/services/{id}/runtime` 的响应改为新结构（Pod、ReplicaSet、HPA、事件、访问地址）。

**数据层**
- `store/db.py`：连接池、事务与保存点；SQL 只用 PostgreSQL 与 GaussDB / openGauss 共有的语法。去掉进程锁：修订号在服务行锁内生成；发布单用条件更新领取，并由唯一索引保证同一服务只有一个执行中的发布。
- 版本化迁移：1 基线（等价于 v1.3 的结构）、2 环境与配置、3 发布快照。新增命令 `db check`、`db upgrade`、`db import-sqlite`。
- GaussDB：启动时检查是否为 PG 兼容模式；驱动可选 psycopg 或华为的 gaussdb。

**环境与配置**
- 环境档案：对象命名、主机名、节点池调度、拉取密钥、日志目录、默认资源。负载均衡入口：支持 CCE 路径匹配方式。
- 平台 → 项目 → 环境 → 服务四层配置集：
  - 环境变量与敏感变量逐层覆盖，上层可以锁定；配置文件按名称替换。
  - 每次修改生成不可变版本，可以查看差异；敏感值逐项加密，有权限的人可以查看明文，每次查看记审计。
  - 生效配置注明每个键的来源层；任意两个环境可以对比。
- 证书库：解析有效期与指纹，按版本轮换；被引用的证书不能删除。

**托管服务**
- 内置模板 web 生成 ConfigMap、Secret、properties、Service、Deployment、Ingress 与 HPA，对象结构与现有 CCE 服务一致。
- 提交发布时记录配置版本快照，与发布单在同一事务中写入；审批、下发、回滚和重新发布都按快照渲染。发布时可以只换镜像，也可以发布最新配置。
- worker 先对全部对象做服务端 dry-run，任何对象失败都不写入；再用 Server-Side Apply（`fieldManager=swr-builder`）下发。下发规则：
  - 共享配置只升级不降级；
  - 开启 HPA 时先移交副本数；
  - 关闭的 Ingress、HPA 等对象在核对平台标签后删除；
  - 配置变化通过 Pod 模板注解触发滚动。
- 集群中已有、不由平台管理的同名对象：普通发布拒绝且不写入集群，只有“接管发布”会接管。属于平台中其他服务的对象不会被覆盖。
- 预览与服务端校验（含新建前的预览）：显示平台声明的字段与线上值的差异。

**导入已有服务**
- 上传 `kubectl get … -o yaml` 的导出，或直接读取集群。平台推断环境档案和负载均衡入口，把所有服务值相同的键上提到环境层，公共配置文件和证书目录作为环境层配置，并列出不一致的地方和接管后的差异。
- 确认后写入平台（应用、服务、各层配置、证书），再用接管发布交给平台管理；接管发布的镜像沿用线上 digest。

**页面**
- 服务详情改为标签页：
  - 运行状态：Pod 状态（按 kubectl 的规则）、重启次数与最近重启、运行时长、节点与 IP、容器状态；Deployment 条件与滚动进度、ReplicaSet 历史、HPA、Service / Ingress 与访问地址、相关事件；
  - 配置、对象清单、发布记录、修订历史。
- 新页面：新建服务向导，环境中的“导入已有服务”，环境、配置中心、证书、负载均衡入口。
- 服务列表增加服务方式、运行状态、就绪副本与未发布配置几列；运行状态只读取当前页。
- 所有列表都有分页；构建记录、发布记录、审计按页从服务端读取。
- 布局（按 1440 / 1280 / 1024 / 390 宽度与深色模式逐页检查后调整）：
  - 侧栏菜单在 900px 高的屏幕上完整显示，更矮时可滚动的一侧显示阴影；`size="small"` 的表格恢复紧凑行高；
  - 服务详情：设置密钥、删除移入“更多”；概览卡片在较窄的屏幕上不留空位；工作负载信息按宽度分 3 / 2 / 1 列；
    镜像只显示仓库与 digest 前缀（完整值在提示中，可复制）；Deployment 条件都正常时只显示一行；访问地址对齐；
  - 对象清单把对象和服务端校验结果合并为一张表，直接列出变化的字段；
  - 服务列表在 1280 宽度下不再横向滚动；配置卡片的按钮固定在右侧，不再重复类型说明；导入页面两张卡片对齐。

**接口与权限**
- 新接口：
  - 环境、配置与证书：`/api/environments/*`、`/api/config-sets/*`、`/api/certificates/*`、`/api/ingress-profiles/*`；
  - 模板与校验：`GET /api/service-templates`、`POST /api/services/preview`、`POST /api/services/{id}/render`；
  - 服务状态与快照：`POST /api/services/status`、`GET /api/services/{id}/manifest`、`GET /api/services/{id}/effective-config`；
  - 接管与导入：`POST /api/services/{id}/adopt`、`POST /api/environments/{id}/import/analyze`、`POST /api/environments/{id}/import/apply`。
- `POST /api/services` 接受 `mode: managed` 和服务层 `config`。
- 新权限：`service.create`、`environment.config`、`config.manage`、`secret.view`、`certificate.view`、`certificate.manage`。新增内置角色“配置管理员”；命名空间管理员与项目管理员获得本范围的配置权限。
- 修复：系统设置关闭“启用部署中心”后，服务的发布、回滚、重新发布、接管与扩缩容接口仍能提交，不需要审批的发布照常执行（此前只拦截 `/api/deploy/` 与 `/api/deployments`）。现在：
  - 这些接口返回 403 `MODULE_DISABLED`；部署模块的路由在定义处用 `@deploy_module` 标记，不再按路径前缀判断；
  - worker 不再领取发布，排队中的发布取消，已领取的发布在写入集群前停止；已开始写入的发布照常完成；
  - 工作台不列“待我审批”，服务详情与导入页面隐藏发布、回滚、接管与审批操作。
- 修复：早期保存、缺少字段的命名空间策略显示为“需 undefined 人审批”。命名空间与环境接口现在返回按默认值补齐的策略，与发布流程实际使用的一致。

- 修复：接管发布没有构建，发布中心与发布详情此前显示“构建 #null”，现在显示“线上镜像（接管发布）”。

**工程与文档**
- CI 在 PostgreSQL 16 与 openGauss 5.0 上各运行一遍后端测试。`tests/fake_kube.py` 模拟 Kubernetes API，支持 Server-Side Apply。
- 新增 `DESIGN.md`（前端设计规范）、`docs/系统架构.md`、`AGENTS.md`（Codex）与 `CLAUDE.md`（Claude Code，导入 AGENTS.md）；
  `docs/系统设计.md` 按 v2 重写；`docs/开发文档.md` 补充部署模块路由、迁移步骤、托管服务交付、前端开发、测试方法与文档维护。

**尚未实现**
- 配置变更后对受影响服务的批量发布（目前需要在每个服务上“沿用当前镜像发布最新配置”）。
- 服务矩阵、线上字段的自动漂移检查、HCS ELB 证书到期查询。
- 离线包的 PostgreSQL 支持。

# v1.3.0-dev.2

整体重构：账号权限只有一套模型，业务规则集中在服务层，界面以项目为中心组织。权限模型详见 [docs/账号与权限设计.md](docs/账号与权限设计.md)。

**账号与权限**
- 权限只来自授权记录（role_bindings）。`users.role` 只保留为兼容字段，不再参与鉴权；迁移 7 为 `role=admin` 的账号补齐全局平台管理员授权。
- 权限点、分组、可授予范围、隐含权限与内置角色只在 `access/catalog.py` 定义一处；每个请求计算一次 `Principal`，中间件、服务层与发布 worker 共用。
- 路由在定义处用 `@requires` / `@authenticated` / `@public` 声明权限，路由权限表由此生成；路由未声明权限或使用未登记的权限码时启动失败。
- 授予与撤销规则对称：全局角色只有平台管理员能授予或撤销；其他人只能在自己有成员管理权限的项目或命名空间内，授予不超过自身权限、且不含成员管理权限的角色。至少保留一名启用的平台管理员，不能撤销自己的平台管理员授权。
- **新账号默认没有任何权限**，可在创建时附带初始授权（与账号同一事务写入）。修改账号资料不再清空其授权。
- 账号管理员只能管理全局权限是自己子集的账号，不能再通过重置密码接管权限更高的账号。
- 授权过期时间统一换算为 UTC 保存（修复 +08:00 等偏移导致授权晚于预期失效）。

**接口（不兼容变更）**
- 所有列表接口返回 `{items, total}`；删除接口返回 204；唯一约束冲突（重复的项目、集群、命名空间、服务、应用等）返回 409 `CONFLICT`，不再是 500；仓库连接测试失败返回 502。
- 删除 `GET /api/roles`，改用 `GET /api/role-definitions`（角色目录 + 权限目录）。
- `POST /api/users` 支持 `platform_admin` 与 `grants`；`PUT /api/users/{id}` 支持 `platform_admin`，不再改动其他授权。旧的 `role` 字段仍可用（`developer` / `viewer` 转为默认项目上的授权）。
- 发布记录不再返回幂等键、请求摘要、租约等执行器内部字段；审批与拒绝接口返回与 `GET /api/deployments/{id}` 一致的记录。
- 新增：`GET /api/overview`（可见范围汇总与待我审批）、`GET /api/users/{id}`（账号详情与有效权限）、`GET /api/bindings/options`（可授予的范围与角色）、`GET /api/apps/{id}`；`/api/apps`、`/api/jobs` 支持 `project_id` 筛选；`/api/bindings` 支持 `user_id`、`scope_type`、`scope_id`、`role` 筛选。

**业务层**
- 集群、命名空间、服务、发布、项目、应用、构建的业务规则从路由移入 `services/`，路由只做参数解析与审计。
- 修复：
  - 审批时按 TEXT 列比较发起人 ID，比较结果错误；
  - 集群 CA 无效、历史构建缺失时重新发布，两处都返回 500（现在返回明确的业务错误）；
  - 登录失败计数没有上限；
  - 每次发布提交记录两条审计；
  - 取消分配命名空间时，被其他项目的服务阻止；
  - `Store._db()` 嵌套调用会死锁（现在立即报错）。

**前端**
- 导航分为 概览 / 交付（构建镜像、构建记录、发布中心）/ 资源（项目、应用、服务）/ 平台（集群与命名空间、访问控制、操作审计、系统设置）。
- 项目详情成为项目工作台：概览、应用、服务、构建记录、发布记录、成员。
- 访问控制合并为一处：账号（详情中查看授权与有效权限）、角色（按分组选择权限并预览隐含权限）、授权。项目成员、命名空间成员与账号详情共用同一个授权对话框，只列出当前账号可授予的组合。
- 按钮按具体项目或命名空间的权限显示；工作台按可见范围显示汇总与待我审批；我的账号增加“我的权限”；发布详情显示审批人姓名。
- 命名空间管理员可以进入“集群与命名空间”管理自己命名空间的成员（看不到集群标签页）。
- Kubernetes 配置从系统设置移到“平台 › 集群与命名空间”。旧地址（`/deploy`、`/resources/*`、`/admin/*`、`/settings`）自动跳转到新位置。

**工程**
- 新增 GitHub Actions：后端测试、前端类型检查与构建，并上传构建好的控制台（`webui` 产物，保留 7 天）。

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
