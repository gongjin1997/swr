# 前端设计规范（DESIGN.md）

适用于 `web/` 下的管理控制台：Vue 3 + TypeScript + Vite + Vue Router + Pinia + Element Plus。新增或修改页面前先读本文。
样式变量的实际取值以 `web/src/styles/tokens.css`、`web/src/styles/main.css` 为准；本文与代码不一致时以代码为准，并修正本文。

相关文档：系统架构见 [docs/系统架构.md](docs/系统架构.md)，前端工程结构见 [docs/开发文档.md](docs/开发文档.md) §3。

## 1. 设计原则

1. **围绕工作流组织页面**。平台的主线是“项目 → 应用 → 构建 → 环境中的服务 → 发布 → 运行”。每个页面回答一个问题：
   这个服务现在是否正常、这次要发布什么、这个环境用了哪些配置。
2. **先结论，后明细**。状态、数量和需要处理的事项放在页面顶部；明细放进标签页、可展开的行和对话框。
3. **信息密度适中**。列表页用默认行高；详情页里的次要表格用 `size="small"`。不放装饰图片，不留大块空白。
4. **同一概念处处一致**。同一个对象在所有页面使用同一个名称、同一种状态颜色、同一种格式（时间、镜像地址、ID）。
5. **默认安全**。危险操作二次确认并放进“更多”；敏感值默认不显示；没有权限的操作不显示。前端隐藏按钮只是体验，
   授权以后端为准。

## 2. 设计令牌

### 2.1 颜色

中性色（`--hi-*`，深色模式在 `main.css` 的 `html.dark` 中覆盖）：

| 变量 | 浅色 | 深色 | 用途 |
| --- | --- | --- | --- |
| `--hi-bg-page` | `#f4f6fa` | `#141922` | 页面背景 |
| `--hi-bg-card` | `#ffffff` | `#1c2330` | 卡片、表格、顶栏 |
| `--hi-bg-soft` | `#f7f8fa` | `#232b39` | 表头、次要区块背景 |
| `--hi-bg-hover` | `#f2f6ff` | `#26334b` | 表格行悬停 |
| `--hi-border` | `#e5e8ef` | `#333e50` | 卡片与分隔线 |
| `--hi-border-light` | `#eef0f4` | `#2b3545` | 表格内分隔线 |
| `--hi-text-1` | `#1d2129` | `#edf1f8` | 标题、正文 |
| `--hi-text-2` | `#4e5969` | `#bac5d6` | 次要文字、表单标签、`.hint` |
| `--hi-text-3` | `#86909c` | `#919fb4` | 说明、分组标题、占位 |
| `--hi-sider-bg` / `--hi-sider-active-bg` | `#ffffff` / `#eef4ff` | `#1c2330` / `#243856` | 侧栏背景 / 当前菜单项 |
| `--hi-scroll-shadow` | `rgba(16,24,40,.16)` | `rgba(0,0,0,.6)` | 可滚动区域边缘的阴影 |

语义色（Element Plus 变量，`tokens.css` 定义）：

| 用途 | 变量 | 浅色值 | 使用场景 |
| --- | --- | --- | --- |
| 主色 | `--el-color-primary` | `#1d63f0`（深色 `#76a4ff`） | 主按钮、链接、当前项 |
| 成功 | `--el-color-success` | `#2e7d32` | 成功、运行正常、当前版本 |
| 警告 | `--el-color-warning` | `#f57c00` | 待审批、有未发布配置、需要处理 |
| 危险 | `--el-color-danger` | `#d93026` | 失败、删除、监听器配置未生效 |
| 信息 | `--el-color-info` | `#8c8c8c` | 排队中、已取消、未读取 |

规则：
- 页面与组件只用变量，不写死颜色。唯一的例外是日志与终端区域（`.code-block`、`LogViewer`、`JobDetail` 的终端），
  它们在两种外观下都是深色背景。
- 颜色不能是唯一的信息来源：状态标签都带文字。

### 2.2 字体与字号

| 用途 | 规格 |
| --- | --- |
| 字体 | `--el-font-family`：系统字体 + PingFang SC / Microsoft YaHei / Noto Sans SC |
| 等宽 | `--hi-font-mono`（`.mono`，12px）：对象名、镜像、digest、路径、键名、ID |
| 数字 | `--hi-font-num`（`.num`，等宽数字）：副本数、计数 |
| 页面标题 | `h1` 20px / 650 |
| 区块标题 | `h3` 14–15px / 600 |
| 正文 | 14px，行高 1.5 |
| 表格 | 13px；表头 12px / 500 |
| 说明 | `.hint` 12px，行高 1.7，`--app-text-2` |
| 概览卡片的数值 | 18px / 650；镜像名 14px 等宽 |

### 2.3 间距、圆角与尺寸

| 项目 | 值 |
| --- | --- |
| 页面内边距 `.page` | 18px 22px 28px；≤767px 时 14px 12px 24px；最大宽度 1680px 居中 |
| 卡片内边距 | 20px；≤767px 时 16px |
| 区块间距 `.section-gap` | 18px |
| 工具栏 `.toolbar` / 筛选栏 `.filter-bar` | 间距 10px / 12px；筛选栏中输入框 280px、下拉框 180px |
| 两列布局 `.grid-2` | 间距 18px；≤1100px 时变为一列 |
| 表单网格 `.form-grid` | `repeat(auto-fit, minmax(260px, 1fr))`，列间距 20px |
| 圆角 | 卡片 10px（`--hi-radius`），对话框 12px，输入框与按钮 6px（`--hi-radius-sm`），菜单项 8px |
| 阴影 | 卡片 `--hi-shadow`（很浅）；不要给普通内容加明显的投影 |
| 外框尺寸 | 侧栏 220px（收起 64px），顶栏 56px（≤767px 时 60px） |

## 3. 布局

### 3.1 外框（`components/AppLayout.vue`）

- 左侧固定侧栏：品牌、按分组的菜单（概览 / 交付 / 资源 / 环境 / 平台）、“收起导航”。收起状态保存在 `localStorage`
  的 `swr.ui.collapsed`。菜单由路由表按权限生成；`meta.deploy` 的页面在部署中心关闭时不显示。
- 菜单要在 1440×900 的屏幕上完整显示平台管理员的全部菜单（当前 15 项）。新增菜单项后在这个尺寸下检查；
  更矮的屏幕菜单可以滚动，可继续滚动的一侧有阴影提示。
- 顶栏：左侧面包屑（分组 / 页面），右侧构建引擎状态、外观切换、账号菜单。顶栏吸顶。
- ≤767px：侧栏隐藏，改为左侧抽屉菜单；顶栏只保留菜单按钮、面包屑、外观与头像。

### 3.2 断点

| 宽度 | 变化 |
| --- | --- |
| ≤1280px | 详情页概览卡片 5 列 → 3 列（镜像卡片占 2 列）；描述列表 3 列 → 2 列 |
| ≤1100px | `.grid-2` 两列 → 一列 |
| ≤767px | 移动布局：抽屉菜单；概览卡片 2 列；描述列表 1 列；筛选控件占满一行；对话框与消息宽度不超过屏幕 |

需要逐页检查的尺寸：**1440×900、1280×800、1024×768、390×844**，以及深色模式。

### 3.3 宽度预算

内容区宽度 = 视口宽度 − 侧栏 220 − 页面左右内边距 44；卡片里的表格再减去卡片内边距 40。

| 视口 | 内容区 | 卡片内表格 |
| --- | --- | --- |
| 1440 | 1176 | 约 1136 |
| 1280 | 1016 | 约 976 |
| 1024 | 760 | 约 720 |

- 列表页所有列的 `width` / `min-width` 之和不超过 **976**，保证 1280 宽度下不横向滚动。
- 1024 宽度允许表格在卡片内横向滚动，但操作列必须 `fixed="right"`，整个页面不能横向滚动。
- 合并信息相近的列来省宽度：例如“提交”列上行是提交人、下行是时间；“项目 / 应用”放在一列的两行。

## 4. 页面模板

### 4.1 列表页

```vue
<template>
  <div class="page">
    <PageHeader title="服务" description="一句话说明这个页面管理什么">
      <el-button :icon="Refresh" @click="refresh">刷新</el-button>
      <el-button type="primary" :icon="Plus" @click="create">新建服务</el-button>
    </PageHeader>
    <el-card>
      <div class="filter-bar">
        <el-input v-model="keyword" placeholder="搜索服务、应用、环境" clearable />
        <el-select v-model="filters.project" clearable placeholder="全部项目">…</el-select>
        <span class="count">共 {{ filtered.length }} 个服务</span>
      </div>
      <el-table v-loading="loading" :data="pager.rows.value" empty-text="暂无可见服务">…</el-table>
      <TablePager v-model:page="pager.page.value" v-model:size="pager.size.value" :total="pager.total.value" />
    </el-card>
  </div>
</template>
```

- 第一列是对象名称：粗体链接到详情；第二行用 `.hint` 放补充信息（只在和名称不同时显示）。
- 操作列固定在右侧，只放 1–3 个 `link` 按钮；更多操作进详情页。
- 所有数量不固定的列表都要分页：数据已全部在前端时用 `usePagination`，后端分页（构建记录、发布记录、审计）用
  `useServerPage` 并把 `limit` / `offset` 传给接口。

### 4.2 详情页

从上到下：

1. `PageHeader`：标题是对象名；`#meta` 插槽放类型 / 状态 / 环境标签和归属链接（项目 / 应用 · 集群 / 命名空间）。
2. 操作按钮：最多一个主按钮；次要操作用默认按钮；低频或危险操作放进“更多”下拉（见 §5.9）。
3. 概览卡片：不超过 5 张，用 `.cards` 网格（见 `ServiceDetailView.vue`），每张是“标签 / 数值 / 一行说明”。
4. 提示：需要用户处理的事项用 `el-alert`（见 §5.7），例如“集群中已有同名对象，尚未由平台接管”“有尚未发布的配置变更”，
   提示里直接给出处理按钮。
5. 标签页：`el-tabs`，当前标签写进地址的 `?tab=`，刷新和分享链接后保持；不可用的标签（如部署中心关闭时的发布记录）
   直接不显示，地址里的无效值回到第一个标签。

### 4.3 向导页

`el-steps simple` + 每步一个 `section` + 底部“上一步 / 下一步”。进入下一步前校验本步；最后一步给出预览
（例如渲染出的对象清单与服务端校验结果）再提交。参考 `ServiceCreateView.vue`、`EnvironmentImportView.vue`。

### 4.4 表单

- `label-position="top"`，字段放在 `.form-grid` 里自动分列；说明文字用 `.hint` 放在控件下方或开关右侧。
- 服务端校验失败的提示原样展示（后端文案已经是中文、可操作的）。
- 编辑会生成新版本的对象（服务修订、配置集、环境档案）时，说明“保存后生成新版本，发布后生效”。

### 4.5 对话框与抽屉

- 对话框用于短表单和确认，常用宽度 520 / 680 / 760px，复杂表单不超过 960px；表单类对话框 `:close-on-click-modal="false"`。
- 抽屉用于查看长内容（构建日志、发布详情），宽度 `min(1000px, 100vw)`。
- 关闭后需要清空的内容用 `destroy-on-close`。

## 5. 组件规范

### 5.1 公共组件

| 组件 | 用途 |
| --- | --- |
| `PageHeader` | 页面标题、说明、`#meta` 与右侧操作按钮。所有页面都用它 |
| `TablePager` | 分页条；总数不超过最小页长时不显示；详情页内用 `compact` |
| `StatusTag` | 构建与发布状态标签（文字与颜色来自 `constants.ts`） |
| `DeployDialog` / `DeploymentLogDialog` | 发布对话框（选择构建与配置）/ 发布详情与日志 |
| `JobDetail` / `LogViewer` | 构建详情 / 增量日志 |
| `FileDropzone` | 上传文件（拖拽或点击，超过上限直接拒绝） |
| `NamespacePolicyForm`、`RegistryOrgsEditor` | 命名空间发布策略、项目的 SWR 授权 |
| `access/*` | 授权表、授权对话框、成员面板、权限选择与展示 |
| `config/*` | 分层配置编辑、生效配置、环境对比、环境档案表单 |
| `runtime/ServiceRuntime` | 服务运行状态（Pod、ReplicaSet、HPA、访问地址、事件） |
| `service/ManagedSpecForm`、`service/ManifestPanel` | 托管服务规格表单、对象清单与服务端校验 |

新功能优先复用这些组件；同样的界面出现第二次时再抽成组件。

### 5.2 按钮

| 层级 | 写法 | 规则 |
| --- | --- | --- |
| 主操作 | `type="primary"` | 每个区域最多一个（页面标题区一个，对话框底部一个） |
| 次要操作 | 默认按钮 | 刷新、修改配置、重新发布 |
| 需要关注的操作 | `type="warning"` | 只用于“接管发布”这类改变归属的操作 |
| 表格内操作 | `link` | 详情、发布、回滚到此；危险操作 `link type="danger"` |
| 危险操作 | 放进“更多”，红色文字 | 删除；必须 `confirmAction(…, true)` 二次确认 |

- 按钮文字是动词短语：“新建服务”“接管发布”“查看明文”，不用“确定”“提交”这类不说明结果的词（对话框底部除外）。
- 常用操作可以带 Element Plus 图标（Refresh、Plus、Upload、Edit、Delete、Clock、View），不引入其他图标库。
- 执行中的按钮用 `:loading="busy"`，防止重复提交（`useAction` 的 `busy`）。

### 5.3 状态标签

状态文字和颜色只在 `web/src/constants.ts` 定义，页面不写死。

| 状态 | 文字 | 颜色 |
| --- | --- | --- |
| `queued` | 排队中 | info |
| `running` / `applying` / `verifying` | 构建中 / 发布中 / 验证中 | primary |
| `pending_approval` | 待审批 | warning |
| `success` | 成功 | success |
| `failed` / `rejected` | 失败 / 已拒绝 | danger |
| `unknown` | 结果待确认 | warning |
| `expired` / `cancelled` | 审批超时 / 已取消 | info |

运行状态（`RUNTIME_STATE_*`）：运行正常 success、滚动中 primary、部分可用 warning、滚动失败 danger，已停止、已暂停、
未找到工作负载、无法读取为 info。Pod 状态文字沿用 kubectl 的 STATUS 列（如 `CrashLoopBackOff`），颜色按 `LEVEL_TAG`。

其他映射：服务方式（托管 / 纳管）、环境级别（开发 info、测试 primary、UAT warning、生产 danger）、
服务端校验结果（新建 success、更新 warning、无变化 info、删除 danger、失败 danger）。

### 5.4 表格

- 主列表用默认尺寸；详情页里的次要表格用 `size="small"`（行内边距 7px，见 `main.css`）。
- 列宽遵守 §3.3 的预算；操作列 `fixed="right"`。
- 长文本：对象名、镜像、路径用 `.mono` + `.break-word`；说明类长句用 `show-overflow-tooltip`。
- `empty-text` 说明缺什么、怎么办，例如“还没有条目，点击‘编辑’添加”“最近没有相关事件（Kubernetes 默认只保留约 1 小时）”。
- 行的明细（字段差异、YAML、容器状态）用 `type="expand"` 展开；展开区里的嵌套表格加 `.nested`（左边距 48px）。
- 有异常的行用 `row-class-name` 标出（例如异常的 Deployment 条件标红）。

### 5.5 描述列表

`el-descriptions border size="small"`。列数随宽度变化：宽屏 3 列、≤1280px 2 列、≤767px 1 列
（用 `composables/useMediaQuery.ts`，参考 `ServiceRuntime.vue`）。标签不折行（`white-space: nowrap`）；
跨整行的内容（容器、条件）`:span` 等于当前列数。

### 5.6 卡片

- 页面主体用 `el-card`；卡片内再嵌套的卡片用 `shadow="never"`。
- 卡片标题区用 `.card-title`（标题在左，操作在右）。并排的两张卡片标题要等高。
- 概览卡片 `.card`：标签 12px `--hi-text-3`，数值 18px / 650，下方一行 `.hint`。数值可以按状态着色
  （`tone-success` / `tone-warning` / `tone-danger`）。

### 5.7 提示（el-alert）

| 类型 | 何时使用 | 例子 |
| --- | --- | --- |
| `error` | 操作失败或无法继续 | 服务端校验未通过、集群信息获取失败 |
| `warning` | 需要处理或有风险 | 需要接管发布、证书 30 天内到期、Ingress 监听器配置未生效 |
| `info` | 当前状态说明 | 有尚未发布的配置变更、部署中心已关闭 |

- 表示持续状态的提示 `:closable="false"`，`show-icon`。
- 标题一句话说明情况；`#default` 里写原因和处理方法，可以放处理按钮。
- 一次性的操作结果用消息（`notifySuccess` / `notifyError`），不用 `el-alert`。

### 5.8 代码与日志

- YAML、JSON 用 `<pre class="code-block">`（深色背景，最高 420px，长内容加 `.tall` 到 640px）。
- 构建与发布日志用 `LogViewer` + `useLogTail` 增量读取，离开页面自动停止。

### 5.9 “更多”下拉

```vue
<el-dropdown trigger="click" @command="onMore">
  <el-button>更多<el-icon class="el-icon--right"><ArrowDown /></el-icon></el-button>
  <template #dropdown>
    <el-dropdown-menu>
      <el-dropdown-item command="secret">设置密钥</el-dropdown-item>
      <el-dropdown-item command="delete" divided class="danger-item">删除服务</el-dropdown-item>
    </el-dropdown-menu>
  </template>
</el-dropdown>
```

危险项放在最后，用 `divided` 与其他项隔开，文字为红色（`.danger-item`）。

### 5.10 分页

`PAGE_SIZES` 为 10 / 20 / 50 / 100；列表默认 20 条，详情页内的表格默认 10 条。筛选条件变化时回到第 1 页
（`usePagination` 的 `resetOn`）；数据变少导致当前页越界时自动退回最后一页。

## 6. 数据展示格式

| 数据 | 格式 | 函数 |
| --- | --- | --- |
| 时间 | `2026-11-11 10:00:00`（本地时区） | `fmtTime` |
| 相对时间 | “3 分钟前”，悬停显示完整时间 | `fmtAgo` + `el-tooltip` |
| 运行时长 | 与 kubectl AGE 一致：“45 秒”“3 小时 5 分”“2 天 4 小时” | `fmtAge` |
| 耗时 | 构建、发布用时 | `fmtDuration` |
| 大小 | `11 B`、`1.2 MB` | `fmtSize` |
| 镜像 | 页面显示“组织/仓库:标签”，按 digest 固定时显示“组织/仓库@sha256:前 12 位…”；完整地址放提示里并提供“复制” | `shortImage` / `shortDigest` |
| 编号 | `#12`，等宽 | — |
| 敏感值 | 不显示值，只显示“已设置（11 B）”或“敏感 · 已设置”；有 `secret.view` 权限时点“查看明文”，后端记审计 | — |
| 空值 | 新代码统一用“—” | — |

数字与中文之间加空格：“共 23 个服务”“重启 7 次”。

## 7. 文案

- 全部使用简体中文，平实直接；不用感叹号，不用“请注意”“成功地”这类空话。
- 术语固定，与后端和文档一致：

| 术语 | 含义 |
| --- | --- |
| 项目 | 权限与资源的归属单位，对应 SWR 授权与命名空间分配 |
| 应用 | 一个 SWR 镜像仓库的构建定义 |
| 构建 | 一次镜像构建任务（累加 / 全量） |
| 环境 | 已纳管的命名空间 + 环境档案 + 环境层配置 |
| 服务 | 应用在一个环境中的部署；托管（平台生成全部对象）或纳管（只更新已有 Deployment 的镜像） |
| 修订 | 服务配置的不可变版本 |
| 配置集 | 某一层（平台 / 项目 / 环境 / 服务）的一组环境变量、敏感变量或文件，每次保存生成新版本 |
| 发布 / 回滚 / 重新发布 / 接管发布 | 发布单的四种类型 |
| 部署中心 | 系统设置中的开关，控制发布、回滚、接管与审批 |

- 提示说明后果和下一步，例如：“集群中已有同名对象，尚未由平台接管；第一次下发请使用‘接管发布’”。
- 错误信息展示后端返回的 `detail`，前端不改写；前端自己的校验文案同样要说明怎么改。
- 确认框标题与触发按钮一致（按钮“删除服务”→ 标题“删除服务”），正文说明影响范围（例如“集群中的对象保留不删除”）。

## 8. 权限、开关与页面状态

- 按钮可见性：全局权限用 `session.canGlobal`，项目内用 `session.canProject(id, p)`，命名空间内用
  `session.canNamespace(id, p)`；服务详情优先用接口返回的 `service.permissions`。没有权限就不显示按钮；
  需要解释为什么不能操作时，显示原因文字（例如“部署中心已关闭”“没有发布权限”），不显示一个点不了的按钮。
- 部署中心（`session.deployEnabled`）关闭时：隐藏发布、重新发布、回滚、接管、审批按钮和发布中心菜单，
  在相关位置用 info 提示说明；不要请求发布记录接口（会返回 403）。
- 加载：卡片或表格上用 `v-loading`；首屏加载失败用页面内提示，操作失败用 `notifyError`。
- 自动刷新：`usePolling`，回调返回 `true` 表示仍有进行中的任务、继续轮询；组件卸载时自动停止。
  运行状态在滚动中或有执行中的发布时每 5 秒刷新。
- 异步操作统一用 `useAction` 的 `run`（自动处理忙碌状态和错误提示）。

## 9. 响应式与可访问性

- 任何宽度下页面本身不能横向滚动；表格可以在卡片内横向滚动（§3.3）。
- 移动端：抽屉菜单；页面标题区的按钮换行；对话框宽度不超过 `calc(100vw - 32px)`。
- 只有图标的按钮必须有 `aria-label`；保留“跳至主要内容”链接和 `:focus-visible` 描边。
- 尊重 `prefers-reduced-motion`（`main.css` 已全局处理）。

## 10. 深色模式

- 外观由 `stores/preferences.ts` 控制：浅色、深色、跟随系统，保存在 `localStorage` 的 `swr.ui.theme`；
  深色时 `<html>` 带 `dark` 类。
- 只使用 `--hi-*` 与 `--el-*` 变量，新颜色先在 `tokens.css` 与 `main.css` 的 `html.dark` 中各定义一次。
- 每次改样式至少看一遍深色模式下的标签（plain 效果）、提示、描述列表和代码块。

## 11. 代码组织

```
web/src/
  api/            http.ts（统一请求、401 与强制改密跳转、X-Requested-With、幂等键）、index.ts（各模块接口）、types.ts（响应类型）
  router/         路由表、旧地址跳转、守卫（登录、改密、权限、meta.deploy）
  stores/         session（当前账号与权限判断、deployEnabled）、catalog、platform、preferences
  composables/    useAction、usePagination / useServerPage、usePolling、useLogTail、useMediaQuery
  components/     公共组件（§5.1）
  views/          按导航分组的页面
  constants.ts    状态、类型、级别的文字与颜色映射
  utils/format.ts 时间、大小、镜像格式化与复制
  styles/         tokens.css（变量）、main.css（全局工具类与 Element Plus 覆盖）
```

- 组件用 `<script setup lang="ts">`，文件顶部用一段注释说明组件做什么、和谁共用。
- 接口类型写在 `api/types.ts` 并与后端响应字段一致；新增字段先改后端，再改类型和页面。
- 样式写在组件的 `<style scoped>`；修改 Element Plus 内部结构用 `:deep()`；跨页面复用的工具类才放进 `main.css`
  （`.page`、`.page-heading`、`.hint`、`.mono`、`.num`、`.break-word`、`.section-gap`、`.toolbar`、`.filter-bar`、
  `.grid-2`、`.form-grid`、`.code-block`、`.card-title`）。
- TypeScript 为严格模式（`noUnusedLocals`、`noUnusedParameters`），提交前运行 `npm run typecheck`。
- 不引入新的 UI 库、图标库或 CSS 框架。

## 12. 页面清单

| 导航 | 页面 | 路由 | 主要组件 |
| --- | --- | --- | --- |
| 概览 | 工作台 | `/dashboard` | 汇总卡片、待我审批、最近构建 |
| 交付 | 构建镜像 / 构建记录 / 发布中心 | `/build`、`/jobs`、`/releases` | `FileDropzone`、`JobDetail`、`DeployDialog` |
| 资源 | 项目、项目详情 / 应用 / 服务、服务详情、新建服务 | `/projects`、`/projects/:id`、`/apps`、`/services`、`/services/:id`、`/services/new` | `ServiceRuntime`、`ManifestPanel`、`ManagedSpecForm` |
| 环境 | 环境、环境详情、导入已有服务 / 配置中心 / 证书 | `/environments`、`/environments/:id`、`/environments/:id/import`、`/config`、`/certificates` | `config/*` |
| 平台 | 集群与命名空间 / 负载均衡入口 / 访问控制 / 操作审计 / 系统设置 | `/platform/kubernetes`、`/platform/ingress-profiles`、`/platform/access`、`/platform/audit`、`/platform/settings` | `access/*`、`NamespacePolicyForm` |
| 个人 | 我的账号 / 修改密码 | `/account`、`/account/password` | `PasswordForm` |

v1.3.0-dev.1 之前的地址（如 `/deploy`、`/resources/services/3`、`/admin/users`、`/settings`）由路由自动跳转到新位置。

## 13. 新页面与改动检查清单

- [ ] 用了 `PageHeader`、`el-card`、`TablePager`，列表有分页，表格有 `empty-text`
- [ ] 状态文字与颜色来自 `constants.ts`；时间、镜像、大小用 `utils/format.ts`
- [ ] 按钮按权限显示；部署模块的操作检查了 `session.deployEnabled`
- [ ] 危险操作在“更多”里并二次确认；敏感值不显示
- [ ] 1440×900、1280×800、1024×768、390×844 下页面不横向滚动，1280 下列表不横向滚动
- [ ] 深色模式下没有写死的颜色
- [ ] 加载中、空数据、接口报错三种状态都有合理显示
- [ ] 文案是简体中文，术语与 §7 一致
- [ ] `npm run typecheck && npm run build` 通过

## 14. 已知问题

- 应用、项目、账号三个列表的列较多，1280 宽度下仍需在卡片内横向滚动（操作列固定在右侧）。后续按 §3.3 合并列。
- 部分旧页面的空值显示为“-”，新代码统一用“—”，旧页面在修改时顺带调整。
