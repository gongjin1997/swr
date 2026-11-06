# 重构设计：Vue 3 前端 + 业务逻辑分层

> 状态：已实施（v1.3.0-dev.1，分支 refactor/v1.3-vue3）｜基线：v1.2.0-dev.2（commit 797de51）｜日期：2026-11-06

## 实施状态与和设计的差异

已完成 P1–P7，B1–B8 全部修复并配测试。实施中与下文设计不同的地方：

| 设计 | 实际 | 原因 |
| --- | --- | --- |
| 数据层目录名 `repositories/` + 门面 | `swr_builder/store/` 包，`Store` 由各领域 mixin 组合 | 测试与 `cloud/__main__.py` 直接 `from swr_builder.store import Store, SCHEMA, utcnow`，包名不变最省迁移成本 |
| 构建模块移入 `build/` | 保持原位置（`builder.py`、`planner.py` 等） | 测试按原模块路径导入；搬目录收益小 |
| `core/` 目录 | 平铺为 `errors.py`、`context.py`、`images.py`、`fileio.py`、`validation.py` | 文件少，不值得多一层目录 |
| `route_permission` 预编译缓存 | 未做 | 约 90 条路由线性匹配，耗时可忽略 |
| 租约过期：已写入标 unknown，未写入重新排队 | 过期的执行中任务一律标 unknown | worker 在 PATCH 返回前状态仍是 applying，无法可靠区分是否已写入集群 |
| Element Plus 按需引入 | 全量引入 | 少两个构建插件依赖；内网工具对首屏体积不敏感 |
| 构建详情路由 `/jobs/:id` | `/jobs?detail=:id`（抽屉）、`/build?job=:id` | 列表与详情同屏，刷新可恢复 |
| Vitest / Playwright 冒烟 | 未加 | 开发沙箱无法安装 npm 依赖，见下 |
| 重的 Store 调用放入 `asyncio.to_thread` | 未做 | 等有性能数据再定（§4.5 已说明） |

**验证情况**：后端 77 个测试通过（原 66 + 新增 11），重构前后 API 路由表（方法、路径、状态码、权限）逐条比对一致。前端在开发沙箱中**未能执行 `npm install`/`vue-tsc`/`vite build`**（无外网）：已用 Prettier 解析全部 `.vue`/`.ts` 文件，并用最小类型桩对全部 TypeScript 与 `<script setup>` 做了 `tsc` 检查；模板表达式与 Element Plus 组件属性的类型检查、实际页面效果需在可联网环境执行 `cd web && npm install && npm run build` 后确认。首次安装后请提交生成的 `web/package-lock.json`。

## 0. 目标与约束

**目标**

1. 删除现有原生 JS 页面（`swr_builder/static/` 全部 10 个文件），用 Vue 3 单页应用重写，功能不减。
2. 重构后端：把 `main.py`、`routes.py`、`store.py` 里混在一起的路由、业务规则、SQL 按层拆开，并修复梳理中发现的缺陷。

**约束**

- 对外 HTTP API 第一阶段**保持兼容**：路径、参数、响应字段不变，API 令牌调用方不受影响。需要破坏性调整的点单独列在 §4.6，放到后续版本。
- 鉴权模型不变：`swr_session` Cookie + `X-Requested-With: XMLHttpRequest` 防 CSRF + Bearer API 令牌；路由权限表"未登记即拒绝"（fail closed）。
- 构建 worker 仍为单进程，本次不做跨主机高可用。
- 部署环境多为内网：前端依赖在 Docker 多阶段构建中安装，npm 源可配置（与现有 `PIP_INDEX_URL` 做法一致），运行时镜像**不需要 Node**。
- 每个重构步骤都要让现有 `tests/` 保持通过；行为变化（缺陷修复）必须附带新测试。

## 1. 现状问题摘要

| 区域 | 问题 | 位置 |
| --- | --- | --- |
| 前端 | 6 个脚本共享全局作用域，靠加载顺序互相调用（`initPortal`、`can`、`renderPassword`、`state.apps`） | `static/index.html:172-177` |
| 前端 | 两套渲染方式：静态 HTML + 事件委托，和整段 `innerHTML` 重绘 + 逐个 `.onclick` | `app.js`、`resources.js` |
| 前端 | 无路由，刷新回到首页；401 跳转丢失 query；`PASSWORD_CHANGE_REQUIRED` 运行时不处理 | `app.js:41-51`、`portal.js` |
| 前端 | 服务发布用裸 `fetch` 绕过统一错误处理；`registry_orgs`、服务 spec 用原始 JSON 文本框编辑 | `resources.js:52-62, 251-261` |
| 前端 | 单一共享弹窗，无焦点管理，无法叠加；`window.confirm` 与自定义弹窗混用 | `index.html:162-168` |
| 后端 | `create_app` 一个闭包约 460 行，含中间件、辅助函数、构建/应用/任务接口 | `main.py:209-672` |
| 后端 | `mount_routes` 一个闭包覆盖整个文件（约 1030 行），无 `APIRouter` | `routes.py:27-1054` |
| 后端 | 循环依赖：`routes.py` 在函数内 `from .main import _read_log` | `routes.py:1033` |
| 后端 | `Store` 约 120 个方法、15 个领域，内含业务规则（审批人数、自审禁止、默认项目删除规则、角色映射） | `store.py` |
| 后端 | 镜像地址解析实现了 3 份；迁移样板代码复制 5 份；Dockerfile `FROM` 解析循环复制 2 份 | `store.py:318-331, 776-790`、`main.py:338-412` |
| 后端 | 错误表达不统一：`ValueError`/`RuntimeError('REVISION_CONFLICT')`/`FileExistsError`/匹配 `'UNIQUE constraint failed'` 字符串 | `store.py`、`routes.py` |
| 后端 | 中间件统一审计 + 处理函数显式 `audit()`，项目/集群/命名空间/服务/授权操作各写两条审计 | `main.py:304`、`routes.py` |
| 后端 | 列表响应有的是 `{items,total}`，有的是裸数组；删除有的回 `{ok:true}`，有的回 204 | 多处 |

### 1.1 已确认的缺陷（重构中修复，每项配测试）

| # | 缺陷 | 位置 | 影响 |
| --- | --- | --- | --- |
| B1 | `SysConfig.update` 失败回滚时再次调用全部 `on_change` 回调；若失败源于 `manager.resize`，回滚时会再抛异常，调用方收到原始异常而非 `SettingsError`；且回调先于 `save_settings` 执行 | `sysconfig.py` `update()` | 设置保存失败时运行态可能不一致 |
| B2 | 构建先取全局并发信号量、再取同仓库锁：同仓库排队的任务占着并发名额（队头阻塞） | `builder.py:176-182` | 并发 2 时，同一应用连续提交两次会让其他应用全部等待 |
| B3 | `_repo_locks` 只增不减 | `builder.py:88` | 长期运行内存缓慢增长（量小） |
| B4 | 服务发布 worker 循环无异常保护，任一次 DB 异常会让后台任务静默退出，此后所有发布一直排队 | `deploy/manager.py:45-57` | 发布功能整体停摆，需重启 |
| B5 | 过期租约不会被回收、也没有续租；进程内卡住的发布会一直占住该服务，直到重启被标记 `unknown` | `store.claim_next_service_deployment` | 单服务发布阻塞 |
| B6 | `namespace['policy']['rollout_timeout']`、`policy['require_approval']` 直接下标取值；新建策略会补默认值，但早期数据或直接写库的策略缺键时会 `KeyError`，且发生在 PATCH 之后，记录被标为 `unknown` | `deploy/manager.py:141`、`routes.py:748` | 旧数据发布失败 |
| B7 | 审批要求的计算公式写了 3 份（routes 1 份、manager 2 份），写法不一致 | 同上 | 规则改动易漏改 |
| B8 | `get_project`、`get_namespace` 每次查询加载全表再遍历 | `store.py:518, 573` | worker 循环内 O(n) |

> 另有若干"疑似问题"（如冻结截止时间不带时区时视为永久冻结、`get_session` 不过滤过期），实施前逐项写测试确认，确认后再修。

## 2. 总体架构

```
浏览器 ── Vue 3 SPA（/assets/*.js 哈希文件，index.html 外壳）
   │  fetch/XHR，带 X-Requested-With
   ▼
FastAPI
 ├─ web/        中间件（认证、CSRF、权限、安全头、审计）、SPA 托管、权限表
 ├─ api/        APIRouter，每个领域一个文件；只做参数解析、调用服务、组装响应
 ├─ services/   业务规则（构建提交、应用镜像策略、命名空间策略、服务发布、审批、授权…）
 ├─ repositories/  SQL 访问，按领域拆分；Store 作为门面保留
 ├─ build/      构建流水线（planner、versioning、dockerfile、registry、builder）
 ├─ deploy/     部署适配器、发布 worker、Kubernetes 客户端
 └─ cloud/      HCS IAM（AK/SK 签名）
```

依赖方向只能自上而下：`api → services → repositories / build / deploy / cloud → core`。`core` 只放无依赖的基础件（错误类型、时间、镜像地址解析、上下文定义）。

## 3. 前端设计（Vue 3）

### 3.1 技术选型

| 项 | 选择 | 理由 |
| --- | --- | --- |
| 框架 | Vue 3.5+，`<script setup>` + Composition API | 用户指定 |
| 语言 | TypeScript | 接口字段多（应用 20+ 字段、命名空间策略嵌套），类型能挡住大部分字段拼写错误 |
| 构建 | Vite | Vue 官方推荐；产物为带哈希的静态文件，后端直接托管 |
| 路由 | Vue Router 4，history 模式 | 解决刷新丢失页面、深链接、`next` 回跳 |
| 状态 | Pinia | 只放跨页共享状态：当前用户/权限、平台信息、应用列表缓存 |
| 组件库 | Element Plus（按需引入） | 中文后台常用，表格、表单校验、对话框、日期时间、穿梭框齐全；内置中文语言包 |
| 测试 | Vitest + @vue/test-utils；Playwright 冒烟 | 与 Vite 同栈 |
| 代码规范 | ESLint（`eslint-plugin-vue`）+ Prettier + `vue-tsc` 类型检查 | |

备选：Naive UI（更轻、TS 友好），Ant Design Vue。选 Element Plus 的主要原因是表格/表单生态和团队熟悉度，若团队已有其他组件库可替换，§3.4 的业务组件不受影响。

运行时只使用 Vue 的 runtime-only 构建（SFC 预编译），不需要 `unsafe-eval`，可以启用严格 CSP（§3.7）。

### 3.2 目录结构

```
web/                         # 前端工程（新增，仓库根目录）
  package.json  package-lock.json  vite.config.ts  tsconfig.json  .npmrc.example
  index.html
  src/
    main.ts                  # 创建 app、注册 router/pinia/Element Plus
    App.vue                  # 布局外壳：顶栏、导航、<RouterView>
    router/
      index.ts               # 路由表 + 全局守卫（登录、改密、权限）
    api/
      http.ts                # fetch 封装、错误归一化、401/改密拦截、上传进度
      types.ts               # 后端响应类型
      auth.ts account.ts users.ts apps.ts builds.ts jobs.ts
      projects.ts clusters.ts namespaces.ts services.ts deployments.ts
      bindings.ts settings.ts system.ts legacyDeploy.ts
    stores/
      session.ts             # 当前用户、permissions、can()、site_name、deploy_enabled
      platform.ts            # /api/info：SWR、Docker 状态、max_upload_mb
      apps.ts                # 应用列表缓存（构建页、记录筛选、服务表单共用）
    composables/
      usePolling.ts          # 带退避、可取消、组件卸载自动停止的轮询
      useLogTail.ts          # 按 offset 增量读取日志（构建与发布共用）
      useConfirm.ts          # 统一确认框（替代 window.confirm）
      useAsyncAction.ts      # 按钮 loading + 错误提示（替代 setBusy）
    components/
      AppHeader.vue  StatusTag.vue  CopyText.vue  OneTimeSecret.vue
      LogViewer.vue          # 行着色、贴底自动滚动、aria-live
      JobSteps.vue           # 解析版本→登录→构建→推送→完成
      FileDropzone.vue       # 拖拽/点击选择、同名替换、大小校验、目标路径预览
      RegistryOrgsEditor.vue # 替代 registry_orgs 原始 JSON
      NamespacePolicyForm.vue  DeployWindowEditor.vue
      ServiceSpecEditor.vue  # 表单为主，保留"高级：JSON"切换
    views/
      LoginView.vue
      build/BuildView.vue  build/JobPanel.vue
      jobs/JobListView.vue  jobs/JobDetailDrawer.vue
      apps/AppListView.vue  apps/AppFormDialog.vue  apps/DockerfileImport.vue
      resources/ProjectsView.vue ClustersView.vue NamespacesView.vue ServicesView.vue
               ServiceDetailView.vue BindingsView.vue
      deploy/LegacyDeployView.vue
      settings/SettingsView.vue
      admin/UsersView.vue  admin/AuditView.vue
      account/AccountView.vue  account/ChangePasswordView.vue
    constants/
      status.ts              # MODE_TEXT、STATUS_TEXT（补齐 expired 等）、终态集合
      roles.ts               # 作用域 → 可授角色
    utils/format.ts          # 大小、时间、耗时
    styles/tokens.css        # 沿用现有配色变量，映射到 Element Plus CSS 变量
```

### 3.3 路由与页面

| 路径 | 视图 | 权限（`meta.perm`） | 对应现有 |
| --- | --- | --- | --- |
| `/login` | LoginView | 公开 | `login.html` |
| `/build` | BuildView | `build.run` | tab-build |
| `/jobs`、`/jobs/:id` | JobListView + 详情抽屉 | `job.view` | tab-jobs |
| `/apps` | AppListView | `app.view`；新建/编辑/删除 `app.manage` | tab-apps |
| `/resources/projects` | ProjectsView | `project.view` | resources › 项目 |
| `/resources/clusters` | ClustersView | `cluster.manage` | resources › 集群 |
| `/resources/namespaces` | NamespacesView | `namespace.view` | resources › 命名空间 |
| `/resources/services`、`/resources/services/:id` | ServicesView / ServiceDetailView | `service.config` 或 `namespace.view` | resources › 服务 |
| `/resources/bindings` | BindingsView | `user.manage` / `project.member.manage` / `namespace.member.manage` 任一 | resources › 成员授权 |
| `/deploy` | LegacyDeployView | `deploy.view` 且 `deploy_enabled` | tab-deploy |
| `/settings` | SettingsView | `settings.manage` | tab-settings |
| `/admin/users`、`/admin/audit` | UsersView / AuditView | `user.manage` / `audit.view` | tab-users |
| `/account`、`/account/password` | AccountView / ChangePasswordView | 已登录 | tab-account |
| `/` | 重定向：有 `build.run` 去 `/build`，否则 `/jobs` | | 现有默认页逻辑 |

**全局守卫顺序**

1. 未加载用户时调用 `GET /api/auth/me`；401 → `/login?next=<完整 fullPath>`（保留 query 和 hash）。
2. `must_change_password` 为真 → 除 `/account/password` 外一律重定向到该页，导航只显示退出。
3. `meta.perm` 不满足 → 跳到用户有权限的第一个页面，并提示"没有访问权限"。
4. 登录页的 `next` 只接受以 `/` 开头、不以 `//` 开头且不是 `/login` 的站内路径（保留现有防开放重定向逻辑）。

导航菜单由同一份路由表生成，按权限过滤，避免菜单与路由守卫两套规则不一致（现有 `renderResources` 默认参数与 `loadResources` 规则就不一致）。

### 3.4 关键交互设计

**构建页（BuildView）**：左侧表单、右侧实时日志，布局沿用现有。

- 选应用 → 自动读取 `/api/apps/{id}/versions`，展示上一版本、新版本、层数/上限（≥80% 标黄）、大小、架构、推送时间。
- 构建方式单选：无基础镜像且全量模板含 `{{BASE_IMAGE}}` 时禁用"全量构建"；仓库无版本时提示自动改为全量。
- 新版本号：留空自动；输入时实时校验正则与重复版本。
- `FileDropzone`：显示每个文件 → 镜像内路径；超过 `max_upload_mb` 在选择时即提示。
- 提交使用 XHR 以获得上传进度（`fetch` 不支持上传进度），401 同样走统一跳转。
- 提交成功后右侧切到该任务日志，并把 `?job=<id>` 写进 URL，刷新后可恢复。
- `localStorage` 只保留"上次选择的应用"。`operator` 字段后端已忽略（以登录用户为准），前端不再显示该输入框。

**日志（LogViewer + useLogTail）**：构建日志和发布日志共用一套。

- 轮询 `GET .../log?offset=`：默认 1 s，上一次返回超过 200 KB 立即续读，出错退避到 3 s；`finished` 后停止。
- 状态或步骤变化时才重新拉取任务详情（与现有一致，避免多余请求）。
- 只有在底部 48 px 以内才自动滚动；超过 2 万行时只渲染最后 2 万行并提供"下载完整日志"。
- 组件卸载、抽屉关闭、路由切换时自动停止轮询（现有实现靠手动 `destroy()`）。

**应用表单（AppFormDialog）**：字段定义沿用 `APP_FORM` 的分组（基本信息 / 构建设置 / 版本规则 / Dockerfile 模板），改为 Element Plus 表单 + 校验规则：必填、镜像名小写、`max_layers` 0–127、标签正则可编译。"从现有 Dockerfile 导入"保留，只覆盖空字段的行为不变。

**资源管理**：

- 项目的 SWR 授权用 `RegistryOrgsEditor`（组织 + 前缀标签输入，空前缀 = 全部仓库），不再要求手写 JSON。
- 分配命名空间改为穿梭框，保存时一次性提交差异（见 §4.6 的批量接口；第一阶段仍逐个调用现有 PUT/DELETE，失败时提示哪些成功哪些失败）。
- 命名空间策略：`NamespacePolicyForm` 含审批开关/人数、最大副本、允许的组织、冻结（日期时间选择器，提交 ISO 8601 带时区）、发布窗口编辑器（星期多选、起止时间、时区下拉，默认 Asia/Shanghai）。
- 服务详情独立成页 `/resources/services/:id`：当前/已部署修订、修订历史、修订对比（调用已有 `/revisions/diff` 接口，现有 UI 没用上）、发布、审批。解决共享弹窗无法叠加的问题。
- 服务发布：每次打开发布对话框生成一个 `Idempotency-Key`，重复点击提交复用同一个 key；走统一的 `http.ts`。
- 授权过期时间改为日期时间选择器（现有是自由文本 ISO）。

**设置页**：字段仍由后端 `/api/settings` 返回的元数据驱动渲染；环境变量锁定的字段只读并显示来源；只提交改动项和清空项。

### 3.5 API 客户端（`api/http.ts`）

```ts
export class ApiError extends Error {
  constructor(public status: number, message: string, public code?: string, public detail?: unknown) { super(message) }
}

export async function request<T>(path: string, opts: { method?: string; json?: unknown; headers?: Record<string, string>; signal?: AbortSignal } = {}): Promise<T> {
  const headers: Record<string, string> = { 'X-Requested-With': 'XMLHttpRequest', ...opts.headers }
  let body: BodyInit | undefined
  if (opts.json !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(opts.json) }
  const res = await fetch(path, { method: opts.method ?? 'GET', headers, body, signal: opts.signal, credentials: 'same-origin' })
  const data = res.status === 204 ? null : await res.json().catch(() => null)
  if (res.ok) return data as T
  const err = toApiError(res.status, data)          // detail 为字符串或 422 数组都转成可读文本
  if (res.status === 401) onUnauthorized()          // 跳 /login?next=当前 fullPath
  if (err.code === 'PASSWORD_CHANGE_REQUIRED') onPasswordChangeRequired()
  throw err
}
```

- `upload(path, formData, onProgress)`：XHR 实现，同样的请求头与错误处理。
- 各领域模块只导出带类型的函数，例如 `apps.list(): Promise<App[]>`、`jobs.log(id, offset)`。
- 列表接口裸数组与 `{items,total}` 的差异在这一层抹平，页面统一拿到 `{items,total}`；后端日后统一格式时只改这一层。

### 3.6 权限

- `stores/session.ts` 提供 `can(perm)`、`canAny(...perms)`，数据来自 `/api/auth/me` 的 `permissions`。
- 模板中用 `v-if="can('app.manage')"`；不做隐藏 DOM 的自定义指令，避免"元素存在但被隐藏"。
- 取消构建按钮：`can('job.cancel') || (can('job.cancel.own') && job.created_by === me.id)`，与现有规则一致。
- 前端隐藏只是体验优化，后端中间件和处理函数照常校验。

### 3.7 构建产物与后端托管

**Vite 配置**：`base: '/'`，产物输出到 `swr_builder/webui/`（加入 `.gitignore`，不提交构建产物），资源在 `assets/` 下带内容哈希。开发时 `vite` dev server 把 `/api` 代理到 `http://127.0.0.1:8080`。

**后端改动（`web/spa.py`）**

| 请求 | 处理 |
| --- | --- |
| `/assets/*` | 公开；`Cache-Control: public, max-age=31536000, immutable` |
| `/login` | 公开；返回 `index.html`，`Cache-Control: no-store` |
| 其他非 `/api/` 且无扩展名的路径 | 需登录（未登录 303 到 `/login?next=`，保持现有测试断言）；返回 `index.html` |
| 未知 `/api/*` | 404 JSON，不回落到页面 |
| `webui/index.html` 不存在 | 返回 503 文本"前端未构建，请执行 `npm run build`"，API 正常可用 |

兜底路由注册在所有 API 路由之后。中间件的公开路径白名单把 `/static/` 改为 `/assets/`。

**安全头**：新增

```
Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'
```

`style-src 'unsafe-inline'` 是因为 Element Plus 运行时会写内联样式；脚本不放开。HTTPS 部署时可选加 HSTS（配置项控制，默认关闭）。

另外：`/docs`、`/redoc`、`/openapi.json` 默认关闭，通过配置 `server.api_docs: true` 打开（目前登录即可访问）。

**Dockerfile（多阶段）**

```dockerfile
FROM node:22-alpine AS web
ARG NPM_REGISTRY=https://mirrors.huaweicloud.com/repository/npm/
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --registry="${NPM_REGISTRY}"
COPY web/ ./
RUN npm run build            # vue-tsc --noEmit && vite build，输出到 /web/dist

FROM python:3.11-slim
# ……现有 docker CLI、pip 安装步骤不变……
COPY swr_builder ./swr_builder
COPY --from=web /web/dist ./swr_builder/webui
```

`.dockerignore` 增加 `web/node_modules`、`web/dist`。源码部署（systemd）方式在 README 增加一步 `cd web && npm ci && npm run build`，或者由 CI 产出包含 `webui/` 的发布包，构建机无需安装 Node。

## 4. 后端设计

### 4.1 目标目录结构

```
swr_builder/
  __main__.py                 # 不变
  main.py                     # 兼容层：re-export create_app、ROUTE_PERMISSIONS、route_permission、PUBLIC_ROUTE、AUTHENTICATED_ROUTE
  app.py                      # create_app()：组装上下文、注册中间件、include_router、生命周期
  core/
    context.py                # AppContext dataclass（替代 SimpleNamespace）
    errors.py                 # DomainError 及子类，见 §4.4
    images.py                 # parse_image_ref 唯一实现（从 registry.py 移出）+ Dockerfile FROM 提取
    timeutil.py               # utcnow 等
    files.py                  # save_upload、read_log（解除 routes→main 循环依赖）
  web/
    permissions.py            # ROUTE_PERMISSIONS、route_permission（逐条迁移，内容不变）
    middleware.py             # 拆成 4 个函数：authenticate / csrf_and_password_gate / authorize / security_headers_and_audit
    spa.py                    # §3.7
    deps.py                   # current_user、ctx 获取等 Depends
  api/                        # 每个文件一个 APIRouter，只做"解析 → 调服务 → 组装响应"
    auth.py account.py users.py roles.py
    apps.py builds.py jobs.py info.py
    projects.py clusters.py namespaces.py services.py bindings.py deployments.py
    settings.py system.py legacy_deploy.py cloud.py
  services/                   # 业务规则，不依赖 FastAPI，可直接单测
    apps.py                   # 应用增改删、镜像策略校验、默认项目授权补全（合并两份 FROM 解析）
    builds.py                 # 创建构建：校验、建任务、保存上传、解压、提交；失败清理
    versions.py               # 版本预览（复用 planner，去掉 main.py 里那份重复计算）
    projects.py               # slug/名称校验（合并 create/update 两份）、命名空间分配
    clusters.py               # HTTPS 校验、连接测试、凭据轮换
    namespaces.py             # normalize_namespace_policy、纳管、RBAC 自检、RBAC YAML
    catalog.py                # 服务 spec 规范化、修订、修订对比、密钥
    releases.py               # 发布排队、幂等、审批规则（唯一一份 required_approvals）
    bindings.py               # 作用域 → 可授角色矩阵、授权校验
    users.py                  # 用户增改删、角色变更 → 授权重写、保护最后一个管理员
    settings.py               # 设置更新（修复 B1）
    audit.py                  # 审计写入规则（解决重复审计）
  repositories/
    db.py                     # Database：连接、锁、transaction() 上下文、IntegrityError → DomainError
    migrations.py             # 迁移注册表 + 统一执行器（去掉 5 份样板）
    apps.py jobs.py users.py sessions.py tokens.py settings.py audit.py
    projects.py clusters.py namespaces.py services.py bindings.py deployments.py
  store.py                    # Store 门面：组合各 repository，保留现有全部公开方法签名
  build/                      # builder.py planner.py versioning.py dockerfile.py registry.py apps 校验
  deploy/
    base.py webhook.py kubernetes.py __init__.py
    k8s_client.py             # 统一的 Kubernetes HTTP 客户端（GET/PATCH/rollout/SSAR），现有 3 处 httpx 构造合一
    worker.py                 # 服务发布 worker（从 manager.py 拆出，修复 B4–B7）
    legacy.py                 # 旧部署目标（仅迁移核对用）
    manager.py                # 兼容层：DeployManager 组合 worker + legacy
  cloud/                      # 不变
  config.py sysconfig.py security.py auth.py authz.py events.py   # 位置不变，内部按需调整
```

**为什么保留 `Store` 门面**：测试和 `cloud/__main__.py` 直接调用 `Store` 的几十个方法，`app.state.store` 也被测试大量使用。门面把调用转发给各 repository，保持签名不变；新代码（services 层）直接依赖具体 repository。门面在所有调用方迁移后可以再决定是否保留。

**为什么 `main.py` 保留为兼容层**：测试 `from swr_builder.main import create_app, ROUTE_PERMISSIONS, route_permission, PUBLIC_ROUTE, AUTHENTICATED_ROUTE`，部署脚本也可能依赖这个入口。

### 4.2 路由层改造

- 每个领域一个 `APIRouter`，在 `app.py` 中按现有注册顺序 `include_router`。顺序敏感的路由（`/revisions/diff` 必须在 `/revisions/{revision_id}` 之前）在同一文件中保持先后。
- **路径参数名保持不变**（`{id}`、`{app_id}`、`{job_id}`…）：`ROUTE_PERMISSIONS` 以 `(method, route.path)` 为键，测试会遍历 `app.routes` 校验每个 API 路由都已登记。
- 权限表第一阶段原样迁到 `web/permissions.py`，删除现有重复的 services/approve/reject 区块（`main.py:116-131` 与 `100-115` 内容相同）。后续可改为在路由声明处标注权限、再生成该表，但不在本次范围。
- `route_permission` 现在每个请求线性扫描 `app.routes`；改为启动时把路由编译成 `(method, compiled_regex, permission)` 列表缓存，语义不变。
- 两个同名 `registry_test` 处理函数改名（`registry_test_current`、`registry_test_candidate`），路径不变。

### 4.3 中间件拆分

现有 `authentication` 中间件（`main.py:263-306`）按顺序做 7 件事。拆成 4 个函数，在同一个中间件里按原顺序调用，便于单测：

1. `authenticate(request)`：公开路径判断、Bearer/Cookie 解析，返回 user 或 401/303。
2. `enforce_csrf_and_password(request, user)`：Cookie 会话的写请求必须带 `X-Requested-With`；`must_change_password` 白名单。
3. `authorize(request, user, permission)`：全局权限 / 作用域权限 / 部署模块开关。
4. `finalize(response, request, user)`：安全头（新增 CSP）、`Cache-Control`、审计。

**审计去重**：以"处理函数显式写的审计"为准。中间件只对未显式审计的写请求补记一条（处理函数写审计时在 `request.state.audited = True` 标记）。审计条数变少属于行为变化，在 CHANGELOG 中说明。

### 4.4 统一错误模型

```python
class DomainError(Exception):
    status = 400; code = "BAD_REQUEST"
    def __init__(self, message: str, *, code: str | None = None, extra: dict | None = None): ...

class NotFound(DomainError):          status, code = 404, "NOT_FOUND"
class Forbidden(DomainError):         status, code = 403, "FORBIDDEN"
class Conflict(DomainError):          status, code = 409, "CONFLICT"
class RevisionConflict(Conflict):     code = "REVISION_CONFLICT"
class IdempotencyConflict(Conflict):  code = "IDEMPOTENCY_CONFLICT"
class PolicyBlocked(Forbidden):       code = "POLICY_BLOCKED"
class UpstreamError(DomainError):     status, code = 502, "UPSTREAM_ERROR"   # SWR / K8s / IAM
```

- 一个全局异常处理器输出 `{"detail": "<中文说明>", "code": "<机器可读>"}`。**`detail` 保持现有文案**，前端和 API 调用方不受影响，只是多了 `code`。
- `repositories/db.py` 把 `sqlite3.IntegrityError` 按约束名映射成 `Conflict`，不再在业务代码里匹配 `'UNIQUE constraint failed'` 字符串。
- `Store` 门面中原来抛 `RuntimeError('REVISION_CONFLICT')`、`FileExistsError`、`PermissionError` 的地方改抛对应子类；子类同时继承原内置异常（如 `class RevisionConflict(Conflict, RuntimeError)`），已有调用方的 `except` 继续生效，迁移完成后再去掉多继承。
- 全局 `ValueError → 400` 处理器保留，但只作兜底，并记录 warning 日志，方便找出把程序错误当成 400 的地方。
- 服务相关接口里的繁体字（"服務"）统一为简体。

### 4.5 数据层

- **`Database`**（`repositories/db.py`）：沿用"每次调用新开连接 + 进程内锁 + `PRAGMA foreign_keys=ON`"的模式，新增 `transaction(immediate=True)` 上下文，让多步操作（如 `create_user` 的插入用户 + 绑定角色）在一个事务里完成。现有 `create_user`、`create_job`、`ensure_default_project_registry_orgs` 的多次取锁改为单事务。
- **迁移**：`migrations.py` 维护 `[(version, name, fn)]` 列表，一个执行器负责"检查记录、校验 name、`BEGIN IMMEDIATE`、提交或回滚、写记录"。已有 5 个迁移的 version/name/checksum 不变，存量数据库无感知。新迁移 v6：为 `deployments(status, lease_until)` 加索引，支持租约回收查询。
- **按主键查询**：`get_project`、`get_namespace` 改为 `WHERE id = ?`（修复 B8）。
- **事件循环阻塞**：Store 是同步调用，直接在 asyncio 中执行。本次不改为异步驱动（改动面太大）；把明显较重的调用（审计分页、任务列表、worker 内的多表读取）放进 `asyncio.to_thread`。是否全面异步化，等有性能数据后再定。
- 删除无调用方的方法：`create_approval`、`mark_service_deployed_revision`、`count_active_admins`、`get_settings_meta`（先确认测试未引用）。

### 4.6 业务流程优化

**构建流水线（`build/builder.py`）**

- 修复 B2：先取同仓库锁，再取全局并发信号量。同仓库的后续任务在仓库锁上等待，不占并发名额。
- 修复 B3：仓库锁在无人等待时从字典删除（引用计数或 `WeakValueDictionary`）。
- `_execute` 拆为 `_plan`、`_login`、`_build`、`_verify_arch`、`_push`、`_tag_latest` 等步骤函数，每步统一"更新 step → 写日志 → 检查取消"。
- 版本预览接口（`/api/apps/{id}/versions`）复用 `planner` 的同一套计算，删除 `main.py` 中重复的 `next_tag` 预估。
- 上传保存、解压、失败清理逻辑从 `create_build` 处理函数移到 `services/builds.py`，处理函数只剩参数解析。

**服务发布（`deploy/worker.py`、`services/releases.py`）**

- B7：审批人数规则只保留一份：

  ```python
  def required_approvals(policy: dict, tier: str) -> int:
      if policy.get("require_approval", True) or tier == "prod":
          return max(1, int(policy.get("min_approvals", 1)))
      return 0
  ```

  排队时和 worker 复核时都调用它。缺省值取"需要审批"，与 `normalize_namespace_policy` 的默认值一致，属于安全侧默认。
- B6：读取策略统一经过 `effective_policy(namespace)`，用 `normalize_namespace_policy` 的默认值补齐缺失键，不再直接下标访问。
- B4：worker 主循环每轮包 `try/except`，异常记日志并退避 5 s 后继续；`stop()` 时正常退出。
- B5：
  - 运行中每 60 s 续租（`lease_until = now + 300s`），续租失败（fencing token 不匹配）立即停止写入 Kubernetes。
  - `claim_next` 可回收 `lease_until` 已过期的 `applying`/`verifying` 记录：已开始写入的标为 `unknown`，未写入的重新排队，并在发布日志里写明原因。
  - 默认租约从 4500 s 缩短到 300 s（靠续租维持）。
  - worker_id 改为 `主机名:pid:随机后缀`，便于排查。
- `_run_service_deployment` 中重复两次的"权限 + 策略 + 审批 + 镜像组织"检查提取为 `precheck(deployment, namespace)`，PATCH 前用最新命名空间数据再调用一次（保持现有"写入前复核"的安全语义，测试 `test_service_worker_rechecks_policy_before_kubernetes_write` 覆盖）。
- Kubernetes GET/PATCH/rollout 轮询统一到 `deploy/k8s_client.py`，旧部署适配器 `KubernetesDeployer` 和 worker 共用。

**设置（`services/settings.py`，修复 B1）**

1. 校验并计算变更。
2. 写库 `save_settings`（失败直接返回错误，运行态未改）。
3. 应用到内存 `Settings` 并调用 `on_change` 回调。
4. 回调失败：恢复内存旧值和数据库旧值，**回滚时逐个调用回调并单独捕获异常**，最终统一抛 `SettingsError`。
5. "有构建运行时禁止修改"的判断改为调用 `BuildManager.has_running_jobs()`，不再读私有字段 `_tasks`。

**其他**

- 镜像地址解析统一到 `core/images.py`（替换 `store.py` 两份手写解析）。
- 用户角色 → 授权的映射只保留一份（`services/users.py`），迁移 v1 内的那份因为必须冻结历史行为，保留原样并加注释。
- `config.py` 不再 import `cloud.hcs`：`HCSProfile` 移到 `config.py`，`cloud/hcs.py` 反向引用。

**放到后续版本的破坏性调整**（本次只在前端 API 层抹平，不改后端）

- 所有列表统一 `{items, total}`（目前 `/api/apps`、`/api/users`、`/api/deploy/targets`、`/api/account/tokens`、`/api/account/sessions` 返回裸数组）。
- 所有删除统一 204。
- 测试类接口失败时返回非 200（目前 `{ok:false}` + 200）。
- 新增 `PUT /api/projects/{id}/namespaces` 批量替换分配。
- 去掉 `/api/builds` 里已被忽略的 `operator` 字段。

## 5. 实施计划

每个阶段单独一个 PR，阶段内每次提交都跑全量测试。

| 阶段 | 内容 | 主要风险 | 验收 |
| --- | --- | --- | --- |
| P0 | 准备：可联网（或内网镜像）的 CI，`pytest` + 前端 `npm ci && npm run build && vitest`；记录当前测试基线 | 无 | CI 绿 |
| P1 | 机械拆分 I：`core/`（files、images、errors、context）、解除循环依赖、`web/permissions.py`、中间件拆成 4 个函数 | 导入路径遗漏 | 现有测试全过；`main.py` 兼容导出 |
| P2 | 机械拆分 II：`routes.py`、`main.py` 处理函数迁到 `api/*.py` 的 APIRouter，内容不改 | 路由注册顺序、参数名 | 现有测试全过；新增测试：路由表与 P1 前完全一致（method+path 列表快照） |
| P3 | 数据层：`repositories/*` + `Store` 门面 + 迁移执行器 + 按主键查询 | 迁移校验和 | 现有迁移测试通过；新增：旧版数据库文件升级测试 |
| P4 | 服务层：业务规则移入 `services/*`，统一错误模型（加 `code`） | 错误码/文案变化 | 现有测试全过；新增各 service 单测 |
| P5 | 缺陷修复 B1–B8，每项一个提交 + 测试 | 发布 worker 行为变化 | 新增测试：设置回滚、同仓库排队不占名额、worker 异常存活、租约续租与回收、缺键策略 |
| P6 | 前端：`web/` 工程、全部页面、SPA 托管、CSP、Dockerfile 多阶段；删除 `swr_builder/static/` | 功能遗漏 | §6 功能核对表逐项通过；Playwright 冒烟 |
| P7 | 文档：README 部署步骤、开发文档、CHANGELOG（v1.3.0） | | |

P1–P5 与 P6 可以并行：前端只依赖现有 API，P6 不需要等后端重构完成。

**回退方案**：每个阶段独立可回滚。P6 合并前，新前端可先放在 `/next/` 前缀下与旧页面并存一个版本周期（可选，需要的话在 `spa.py` 里多挂一个前缀）。

## 6. 前端功能核对表（P6 验收用）

- [ ] 登录：记住登录、错误提示、锁定提示（429）、`next` 回跳且防开放重定向、初始密码文件提示
- [ ] 首次登录 / 重置后强制改密，改密前其他页面不可进入
- [ ] 顶栏：站点名、SWR 状态（点击测试连接，需 `settings.manage`）、Docker 状态、账号、退出
- [ ] 构建：选应用、版本信息、层数预警、构建方式切换规则、基于版本、版本号校验、拖拽上传、zip 解压、备注、上传进度、Dockerfile 预览、提交后实时日志、取消、刷新后恢复进行中任务
- [ ] 构建记录：按应用/状态筛选、分页加载、进行中自动刷新（4 s）、日志详情、复制镜像地址
- [ ] 应用：列表、新建/编辑（4 组字段 + 校验）、Dockerfile 导入、删除确认、去构建
- [ ] 项目：列表、新建/编辑（SWR 授权可视化编辑）、分配命名空间
- [ ] 集群：列表、纳管（Token、CA）、连接测试
- [ ] 命名空间：列表、纳管、策略编辑（审批、副本、组织、冻结、发布窗口）
- [ ] 服务：列表、创建（项目 → 应用/命名空间联动）、详情、修订历史与对比、新建修订、发布（幂等键）、审批/拒绝、发布日志
- [ ] 成员授权：列表、新增（按自身权限限定作用域与角色）、撤销
- [ ] 旧部署中心：目标列表、编辑、连接测试（Webhook 二次确认）、部署记录、适配器列表；`deploy_enabled=false` 时整页隐藏
- [ ] 系统设置：分组字段、环境变量锁定、只提交变更、SWR/HCS 测试、有构建运行时的提示
- [ ] 账号管理：用户列表、新建/编辑/删除/重置密码（一次性展示）、角色权限矩阵、审计日志分页
- [ ] 我的账号：资料、改密、API 令牌（一次性展示）、登录设备下线
- [ ] 深色模式（跟随系统）、键盘可达、弹窗焦点管理、状态不只用颜色表达

## 7. 待确认事项

1. **组件库**：默认 Element Plus，团队是否已有指定组件库？
2. **构建产物分发**：源码部署（systemd）方式下，是在构建机上装 Node 构建，还是由 CI 产出包含前端产物的发布包？
3. **CI 与内网源**：是否有可用的 npm / PyPI 内网镜像地址？当前开发沙箱无外网，P0 需要它才能跑测试和前端构建。
4. **审计去重**（§4.3）会减少审计条数，是否需要保留"每个写请求都有一条"的旧行为？
5. **§4.6 破坏性调整**：是否有外部系统用 API 令牌调用平台接口？如果没有，可以把列表格式统一等调整提前到本次。
6. **新旧前端并存**：是否需要 `/next/` 灰度一个版本周期？
