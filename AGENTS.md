# AGENTS.md

本文件是编码代理（Codex 等）在本仓库工作的规则。Claude Code 读取 `CLAUDE.md`，它导入本文件，规则以本文件为准。
人工开发说明见 `docs/开发文档.md`。文档、提交信息和界面文案都用简体中文。

## 项目

SWR 镜像构建平台 v2（swr-builder）：上传构建产物 → 累加 / 全量构建镜像并推送华为云 SWR → 在已纳管的 Kubernetes
命名空间中新建托管服务或纳管已有 Deployment → 按审批策略发布、回滚 → 查看运行状态。单进程 FastAPI 应用，
数据存 PostgreSQL 或 GaussDB（PG 兼容模式），前端是 Vue 3 控制台，由后端托管。

## 目录

| 路径 | 内容 |
| --- | --- |
| `swr_builder/api/` | FastAPI 路由：参数解析、调用服务层、审计 |
| `swr_builder/services/` | 业务规则与资源级权限判断（不依赖 FastAPI） |
| `swr_builder/store/` | 数据访问：手写 SQL、连接池（`db.py`）、表结构（`schema.py`）、迁移（`migrations.py`） |
| `swr_builder/delivery/` | 托管服务：规格校验、渲染（纯函数）、快照、Server-Side Apply 下发、导入分析 |
| `swr_builder/deploy/` | Kubernetes 客户端、kubeconfig 解析、发布 worker、旧部署适配器 |
| `swr_builder/access/` | 权限目录与内置角色（`catalog.py`，唯一定义处）、请求主体 |
| `swr_builder/web/` | 中间件、路由权限声明（`guard.py`）与权限表、前端托管 |
| `swr_builder/*.py` | 构建（`builder.py`、`planner.py`、`registry.py`、`dockerfile.py`、`versioning.py`）、配置、安全、认证 |
| `web/` | Vue 3 + TypeScript + Vite + Element Plus 控制台；构建产物输出到 `swr_builder/webui/`（不提交） |
| `tests/` | pytest；`fake_kube.py`（模拟 Kubernetes API）、`mock_registry.py`、`fake_docker.py` |
| `docs/` | 系统架构、系统设计、开发文档、权限设计、v2 设计、测试报告 |
| `deploy/`、`scripts/` | 部署文件、systemd、compose、ARM64 离线包（离线包尚未支持 PostgreSQL） |

## 命令

后端（Python 3.11+）：

```bash
pip install -r requirements-dev.txt
# 测试需要一个有 CREATEDB 权限的 PostgreSQL 账号，每个测试新建并删除自己的数据库
export SWR_TEST_DATABASE_URL=postgresql://postgres:<口令>@127.0.0.1:5432/postgres
pytest -q                                          # 全量
pytest -q tests/test_managed_services.py -k adopt  # 只跑相关测试
python -m compileall -q swr_builder
```

运行：

```bash
cp config.example.yaml config.yaml   # 设置 registry、database.url 等
export BUILDER_DATABASE_PASSWORD=<数据库口令>
python -m swr_builder -c config.yaml           # 启动时自动执行迁移，默认端口 8080
python -m swr_builder db check -c config.yaml  # 也有 db upgrade、db import-sqlite --from data/builder.db
```

前端（Node 18.18+，CI 用 22）：

```bash
cd web
npm ci
npm run typecheck    # vue-tsc，严格模式
npm run build        # 输出到 swr_builder/webui/
npm run dev          # http://localhost:5173，/api 代理到 8080
```

CI（`.github/workflows/ci.yml`）在 PR 上运行三个任务：后端测试（PostgreSQL 16）、后端测试（openGauss 5.0）、
前端类型检查与构建。三个都必须通过。

## 后端约定

- **分层**：路由只做参数解析、调用 `services/`、记审计、返回；业务规则写在 `services/`；SQL 只在 `store/`。
- **错误**：抛 `swr_builder/errors.py` 中的 `DomainError` 子类（`NotFound`、`Forbidden`、`Conflict`、`Unprocessable` 等）
  或 `ValueError`（转换为 400）。错误体是 `{detail, code}`；`detail` 用简体中文，说明怎么处理，不包含凭据、原始云响应或堆栈。
- **权限**：
  - 每个 `/api` 路由在 `@router.<method>` 下面声明 `@requires("权限码")`、`@authenticated` 或 `@public`；
    漏写或使用未登记的权限码，应用启动即失败。
  - 部署模块的路由（发布、回滚、重新发布、接管、扩缩容、审批、发布记录、旧部署目标）再加 `@deploy_module`。
  - 中间件只做粗检查；处理函数必须按具体资源再检查（`services/access.py` 的 `service_or_404`、`writable_service`，
    `me.can_project` / `me.can_namespace`）。无权访问的具体资源返回 404。
  - 新权限只在 `access/catalog.py` 登记，并更新 `docs/账号与权限设计.md`。
- **接口**：列表返回 `{items, total}` 且有界分页（`limit` 不超过 100）；删除返回 204；唯一冲突 409；
  发布类写请求要求 `Idempotency-Key`；写操作用 `web.deps.audit` 记审计。
- **数据库**：
  - 只用 PostgreSQL 与 GaussDB / openGauss 共有的 SQL：主键 `BIGSERIAL` + `RETURNING`；
    **不用** `ON CONFLICT`、`SKIP LOCKED`、IDENTITY、`MERGE`、聚合 `FILTER`、`LATERAL`、数据修改型 CTE。
  - 插入或忽略、插入或更新用 `store/db.py` 的保存点辅助函数；约束显式命名，按 SQLSTATE 与约束名判断冲突，不匹配错误文本。
  - 迁移只追加：在 `store/migrations.py` 末尾登记新版本，不修改已发布的迁移；表结构写在 `store/schema.py`。
  - 每个 Store 方法一个短事务；同一线程内不要嵌套 `_db()`；事务中不要等待网络请求；SQL 参数绑定，不拼接用户输入。
- **集群**：写入只发生在 `deploy/worker.py`；接口里只能读取或做 `dryRun=All` 的服务端校验。渲染（`delivery/render.py`）
  是纯函数，预览、校验和 worker 共用，不要另写一份对象拼装逻辑。
- **敏感值**：用 `SecretBox` 加密保存；接口响应、日志、渲染快照中不出现明文；查看明文需要 `secret.view` 并写审计。
- **风格**：类型注解；模块和关键函数写中文 docstring，说明规则和原因；不加没有用到的抽象和配置项。

## 前端约定

- 修改 `web/` 前先读 `DESIGN.md`。只用 Element Plus 组件和 `web/src/styles` 中的变量；不写死颜色；不引入新的 UI 库或图标库。
- 请求经 `web/src/api`（`http.ts` 统一处理 401、强制改密、CSRF 头和错误）；响应类型写在 `api/types.ts`，与后端字段一致。
- 状态文字与颜色在 `constants.ts`；时间、镜像、大小用 `utils/format.ts`。
- 按钮按权限显示（`session.can*` 或服务详情返回的 `permissions`）；部署模块的操作还要检查 `session.deployEnabled`。
- 列表分页（`usePagination` / `useServerPage` + `TablePager`）；异步操作用 `useAction`；危险操作 `confirmAction(…, true)`。
- 改页面后按 `DESIGN.md` §13 检查 1440 / 1280 / 1024 / 390 宽度和深色模式。

## 安全与数据红线

- **仓库是公开的。** 不要提交真实环境的数据：客户提供的附件、kubectl 导出、配置文件中的命名空间名、服务名、域名、IP、
  ELB 或证书 ID、节点池、目录、配置值、证书。测试、文档和界面示例只用虚构值，例如 `swr.example`、`team/order-api`、
  `orders-sit`、`00000000-0000-4000-8000-000000000000`、`/var/log/apps`。
- 不提交凭据：口令、AK/SK、Token、私钥、kubeconfig、`secret.key`。配置样例用 `<占位符>`。
- 不要绕过或削弱：登录与会话校验、写请求的 `X-Requested-With` 检查、路由权限声明、审批人与发起人分离、
  命名空间策略（冻结、发布窗口、镜像组织）、发布租约与 fencing token。
- 不执行 kubeconfig 的 `exec` / `auth-provider`，不读取 kubeconfig 之外的凭据文件。
- 不要让平台覆盖不归它管理的集群对象：归属规则在 `delivery/apply.py`，只有接管发布可以接管已有对象。

## Git 与提交

- 提交信息用中文：`类型(范围): 说明`，类型为 `feat`、`fix`、`style`、`refactor`、`test`、`docs`、`chore`；
  正文写改动原因和影响。
- 行为变化必须有测试；用户可见的变化写进 `CHANGELOG.md` 最上面的版本段；配置、命令、接口、数据结构变化同步
  `README.md` 与 `docs/`。
- 不要 `git push --force`、`git reset --hard` 或改写已推送的历史，不要 `--no-verify`，除非维护者明确要求。

## 文档索引

| 文档 | 内容 |
| --- | --- |
| `README.md` | 安装、配置、接口列表 |
| `DESIGN.md` | 前端设计规范 |
| `docs/系统架构.md` | 运行时结构、分层、数据、安全、部署 |
| `docs/系统设计.md` | 业务规则、状态机、数据与接口概要 |
| `docs/开发文档.md` | 开发约定、模块职责、测试方法 |
| `docs/账号与权限设计.md` | 权限模型与授予规则 |
| `docs/v2-服务创建与环境配置设计.md` | v2 的方案比较与决定记录 |

## 完成前检查

- [ ] `pytest -q` 通过；改了 SQL 时确认只用了 GaussDB 也支持的语法
- [ ] 改了前端：`npm run typecheck && npm run build` 通过，并按 `DESIGN.md` 检查了页面
- [ ] 新路由声明了权限（部署类路由加了 `@deploy_module`）；新权限已在 `access/catalog.py` 登记
- [ ] 没有提交真实环境数据或凭据
- [ ] `CHANGELOG.md` 和相关文档已更新
