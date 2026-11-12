# SWR 镜像构建平台

面向华为云 Stack SWR 的轻量级镜像编译平台：**页面上传制品 → 自动获取上一版本 → 基于上一版本累加构建 → 推送到 SWR**，全程可视化日志，构建记录可追溯。

- 自动识别上一版本（按版本号或推送时间），自动生成新版本号：默认逢十进一（`1.0.8 → 1.0.9 → 1.1.0`、`1.9.9 → 2.0.0`、`1.0.9-arm64 → 1.1.0-arm64`），应用可改为末位递增（`1.0.9 → 1.0.10`）
- **累加构建**：`FROM 上一版本` + `COPY 本次文件`，只新增 1 层，推送快、节省存储
- **全量构建**：基于基础镜像 + 应用模板（可一键导入现有 Dockerfile）
- 层数保护：上一版本层数达到上限（默认 100，Docker 硬上限 127）时自动改为全量构建
- 架构保护：构建结果与目标架构（如 arm64）不一致时停止推送
- 不覆盖已有版本、同一仓库构建串行、实时日志、取消任务、平台账号登录
- **托管服务**：按模板生成并下发 Deployment、Service、Ingress、HPA 与配置对象；配置按平台 → 项目 → 环境 → 服务分层维护，
  证书集中管理；发布前服务端校验，配置版本随发布单固定，回滚恢复当时的配置
- **导入已有服务**：分析命名空间的 kubectl 导出，把各服务共有的参数上提到环境层，确认后由平台接管

## 1. 环境要求

| 项目 | 要求 |
| --- | --- |
| 构建机 | Linux，已安装 Docker（建议 20.10+），能访问 SWR 地址 |
| Python | 3.11+（使用容器部署时不需要） |
| 数据库 | PostgreSQL 14+（推荐用 Docker 安装独立实例），或以 `DBCOMPATIBILITY 'PG'` 创建的 GaussDB / openGauss 数据库 |
| 架构 | 镜像是 arm64：构建机最好是 arm64；x86 构建机需在应用中设置“构建平台 linux/arm64” |

## 2. 快速开始（源码方式）

```bash
cd swr-image-builder
python3 -m venv venv && . venv/bin/activate
pip install -r requirements.txt

# 构建前端（Vue 3，需要 Node 18.18+；产物输出到 swr_builder/webui/）
(cd web && npm install && npm run build)   # 有 package-lock.json 时用 npm ci

# 数据库：用 Docker 启动一个独立的 PostgreSQL（也可以使用已有的 PostgreSQL / GaussDB）
docker run -d --name swr-postgres --restart unless-stopped -p 127.0.0.1:5432:5432 \
  -e POSTGRES_DB=swr -e POSTGRES_USER=swr -e POSTGRES_PASSWORD='<数据库口令>' \
  -v /opt/swr-builder/pgdata:/var/lib/postgresql/data postgres:16

cp config.example.yaml config.yaml        # 修改仓库地址、组织、用户名、平台登录账号、database.url
export BUILDER_DATABASE_PASSWORD='<数据库口令>'
export SWR_USERNAME='ndc302-region-1_his_beta@<AK>'
export SWR_PASSWORD='<长期有效登录密钥>'    # 不要写进代码仓库

python -m swr_builder -c config.yaml       # 启动时自动执行数据库迁移
```

数据库维护命令：`python -m swr_builder db check`（连接、数据库类型与迁移版本）、`db upgrade`（只执行迁移）、
`db import-sqlite --from data/builder.db`（导入 v1.x 的 SQLite 数据，见第 14 节）、`db status`（不执行迁移，查看迁移与未结束的任务）、
`db apply-sql --dir <目录> --patch <补丁名>`（执行补丁或铺底 SQL，每个脚本只执行一次；`--check` 只校验）。GaussDB 的连接与认证方式见
[v2 设计](docs/v2-服务创建与环境配置设计.md) §8。

浏览器打开 `http://<构建机IP>:8080`，进入独立登录页。未构建前端时页面会提示构建命令，API 不受影响。

前端开发：先按上面启动后端，再执行 `cd web && npm run dev`，浏览器打开 `http://localhost:5173`（`/api` 自动转发到 8080）。构建机不方便安装 Node 时，可以在其他机器构建后把 `swr_builder/webui/` 一起拷贝过去，或直接使用容器部署。

- 首次启动管理员为 **admin**。`server.admin_password` / `BUILDER_ADMIN_PASSWORD` 为空时，自动生成初始密码并写入 `data/admin-initial-password.txt`（权限 0600），不会输出到日志。首次登录必须改密；改密后文件自动删除。
- 如指定初始化密码，请使用强密码；该配置只用于首次创建账号，不是后续密码重置机制。
- 原 v1.0 的 `server.users` 会在空账号库首次启动时迁移为管理员，并要求改密。
- 生产务必使用 HTTPS，配置 `server.secure_cookie: true`，限制访问来源；平台没有“免登录”运行模式。
- 管理员在“平台 › 访问控制”创建账号，并授予项目或命名空间角色（**新账号默认没有任何权限**）；“我的账号”支持资料、密码、我的权限、登录设备和 API 令牌。

> 构建 worker 与旧版部署目标仍限定单进程运行。服务发布队列保存在数据库中，使用租约与 fencing token；跨主机高可用尚未实现。

### 前端工作台

前端沿用 HCS 巡检平台的浅色侧栏、设计令牌和 Element Plus 组件规范，按“项目 → 应用 → 构建 → 服务 → 发布”的交付流程组织。菜单只显示当前账号有权限的入口：

| 分组 | 页面 | 内容 |
| --- | --- | --- |
| 概览 | 工作台 | 可见范围内的项目、应用、服务、构建汇总，待我审批的发布，最近构建 |
| 交付 | 构建镜像 / 构建记录 / 发布中心 | 上传制品构建并查看实时日志；按项目、应用、状态筛选构建；选择成功构建发布到命名空间并审批 |
| 资源 | 项目 / 应用 / 服务 | 项目工作台；服务列表显示托管/纳管、运行状态与未发布的配置；新建服务向导；服务详情（运行状态、配置、对象清单、发布记录、修订历史） |
| 环境 | 环境 / 配置中心 / 证书 | 环境档案（命名、入口、调度、日志目录、默认资源）、环境层配置、环境对比、导入已有服务；平台层与项目层配置；证书库 |
| 平台 | 集群与命名空间 / 负载均衡入口 / 访问控制 / 操作审计 / 系统设置 | 纳管集群与命名空间并设置发布策略；ELB 入口；账号、角色、授权；审计记录；仓库、构建、安全等设置 |

- Kubernetes 集群支持 kubeconfig `kind: Config` 的静态 Token/内嵌证书，不依赖 HCS 配置。
- 侧栏可折叠；手机使用抽屉导航；外观支持浅色、深色与跟随系统，偏好保存在当前浏览器。
- 列表页面都支持分页；构建记录、发布记录、审计按页从服务端读取。

登录后的默认入口为 `/dashboard`。v1.3.0-dev.1 及之前的页面地址（如 `/deploy`、`/resources/services/3`、`/admin/users`、`/settings`）会自动跳转到新位置。前端生产产物仍输出到 `swr_builder/webui/`，由 FastAPI 托管，无需更换部署方式。

## 3. 部署方式

ARM64 / aarch64 内网环境推荐使用离线交付，默认部署到 `/data/swr`、端口 `8082`，详见 [ARM64 离线部署与更新](docs/ARM64离线部署与更新.md)：

- 首次部署用全量离线包 `swr-builder-arm64-<版本>.tar.gz`：平台运行镜像、PostgreSQL 16 镜像（也可改用外部 PostgreSQL / GaussDB）、代码、安装与管理脚本、铺底资源（SQL、业务基础镜像、证书）、锁定的 Python wheel 与源码。`sudo sh install.sh /data/swr 8082` 一次完成。
- 日常更新用补丁包 `swr-patch-<版本>.tar.gz`：不含镜像，可带完整代码、DDL、DML；`swrctl apply` 先预检，再停机、备份（含数据库导出）、切换代码、执行迁移与 SQL，失败时自动恢复。
- 运行依赖变化时用新的全量包 `upgrade.sh` 升级；从 v1（SQLite）迁移时安装到新目录并导入旧数据。

### 3.1 systemd（推荐，直接使用构建机的 Docker）

```bash
useradd -r -G docker swrbuilder
mkdir -p /opt/swr-builder && cp -r . /opt/swr-builder && cd /opt/swr-builder
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp config.example.yaml config.yaml && vi config.yaml
cp deploy/swr-builder.env.example swr-builder.env && chmod 600 swr-builder.env && vi swr-builder.env
chown -R swrbuilder:docker /opt/swr-builder
cp deploy/swr-builder.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now swr-builder
journalctl -u swr-builder -f
```

### 3.2 容器

```bash
docker build -t swr-builder:2.0.0-dev.1 .     # 多阶段构建：先用 Node 构建前端，运行时镜像不含 Node
# 内网环境可指定镜像源：--build-arg NPM_REGISTRY=... --build-arg PIP_INDEX_URL=...
docker run -d --name swr-builder --restart unless-stopped -p 8080:8080 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /opt/swr-builder/config.yaml:/app/config.yaml:ro \
  -v /opt/swr-builder/data:/app/data \
  -e BUILDER_DATABASE_URL='postgresql://swr@<数据库地址>:5432/swr' -e BUILDER_DATABASE_PASSWORD='<数据库口令>' \
  -e SWR_USERNAME='ndc302-region-1_his_beta@<AK>' -e SWR_PASSWORD='<登录密钥>' \
  swr-builder:2.0.0-dev.1
```

也可以使用 `deploy/docker-compose.yml`（平台 + 独立的 PostgreSQL 16 容器，说明见文件头部）。容器内只有 docker CLI，
实际构建由宿主机 Docker 完成。容器内的 `127.0.0.1` 指容器自身，`BUILDER_DATABASE_URL` 要写容器能访问到的数据库地址；
平台与数据库都用容器运行时，推荐直接使用 docker-compose。

### 3.3 Nginx 反向代理（可选）

```nginx
location / {
    proxy_pass http://127.0.0.1:8080;
    client_max_body_size 2g;          # 与 build.max_upload_mb 保持一致
    proxy_request_buffering off;
    proxy_read_timeout 600s;
}
```

## 4. SWR 凭证（重要）

平台使用 `docker login` 推送镜像，并调用 Registry V2 接口读取版本，需要以下任意一种凭证：

1. **长期有效登录指令**（推荐）：SWR 控制台 →“我的凭证 / 登录指令”→ 获取长期有效登录指令
   `docker login -u <区域项目名>@<AK> -p <登录密钥> swr.ndc302-region-1.huaweicloud.cnnc.cn`
   把 `-u` 的值填到 `SWR_USERNAME`，`-p` 的值填到 `SWR_PASSWORD`。
2. **AK/SK**：配置 `SWR_PROJECT`、`SWR_AK`、`SWR_SK`，平台按 SWR 规则自动计算登录密钥（HMAC-SHA256）。

> 资源配置截图中的“秘钥”是 `20~A2~C75292...` 这样的格式，可能是其他系统加密保存后的值，不能直接给 `docker login` 使用。
> 请先在构建机上用上面的命令手动 `docker login` 成功，再把同样的用户名/密钥配置到平台。

账号需要对组织 `metaerp-beta15`（读写）和基础镜像所在组织 `metaerp_sdk`（只读）有权限。

## 5. 使用流程

开始前，平台管理员在“资源 › 项目”新建项目并填写允许推送的 SWR 组织，再在“访问控制”或项目的“成员”标签页给开发人员授予项目角色（如“开发者”）。

1. **项目 › 应用 → 新建应用**：粘贴现有 Dockerfile 点“导入”，自动识别基础镜像、工作目录、目标文件名、属主（`--chown`）；填写镜像名（如 `cnnc-coding-chc-fin`）和构建平台（如 `linux/arm64`）。
   `config.example.yaml` 已按附件的 Dockerfile 预置了 `cnnc-coding-chc-fin` 应用。
2. **构建镜像**：选择应用，页面自动显示上一版本、层数、架构和建议的新版本号。
3. 选择 **累加构建**（默认）或 **全量构建**，可指定“基于版本”和“新版本号”（留空自动递增）。
4. 上传 jar/war/配置文件（可多选；zip 可勾选“解压后合并到目标目录”）。
5. 可先“预览 Dockerfile”，再点“上传并构建”，右侧实时显示日志：解析版本 → 登录仓库 → 构建镜像（含架构校验）→ 推送镜像。
6. **构建记录**：查看历史任务、镜像地址、摘要、日志，运行中的任务可取消。

### 文件放置规则

- 只上传 1 个文件且应用配置了“目标文件名”：重命名为该文件名，例如 `cnnc-coding-fin-1.1.1-SNAPSHOT.jar → /app/cnnc-coding-chc-fin.jar`
- 多个文件：保留原文件名放到目标目录
- zip 勾选解压：按 zip 内目录结构合并到目标目录（自动防路径穿越）

## 6. 构建方式说明

| | 累加构建（incremental） | 全量构建（full） |
| --- | --- | --- |
| Dockerfile | `FROM 上一版本` + `COPY files/ /app/` | 应用的全量模板（来自原 Dockerfile） |
| 层数 | 每次 +1 层 | 重置为 基础镜像层数 + 模板层数 |
| 适用 | 日常发版、替换 jar | 首次构建、更换基础镜像、层数过多、修改 CMD/EXPOSE |
| 自动切换 | 仓库无历史版本 / 层数 ≥ 上限 → 自动改为全量 | — |

> 累加构建时被覆盖的旧 jar 仍保留在下层，镜像体积会逐步增长，建议定期（例如每 20~30 个版本）做一次全量构建。

模板变量：`{{BASE_IMAGE}}`、`{{COPY_FILES}}`、`{{TARGET_DIR}}`、`{{NEW_TAG}}`、`{{PREV_TAG}}`、`{{IMAGE}}`。

## 7. 配置说明

管理员可在“平台 › 系统设置”维护站点、仓库、构建与安全设置；Kubernetes 集群与命名空间在“平台 › 集群与命名空间”管理。Kubernetes 直连 kubeconfig，不要求配置华为云 Stack。优先级为 **环境变量 > 数据库中的页面设置 > config.yaml / 默认值**。环境变量控制的项在页面只读；敏感值不回显，留空保持，明确勾选才清空。设置生效不需要重启；有未结束的构建时不能修改仓库及构建参数。

镜像仓库的“跳过 Registry API TLS 校验”只影响平台读取 Registry API。`docker login/build/push` 由宿主机 Docker daemon 连接仓库；遇到 x509/SAN 错误时，应修复仓库证书，或在 Docker daemon 的 `/etc/docker/daemon.json` 中合并 `insecure-registries` 配置并重启 Docker。该主机级配置不会由平台自动改写或重启。

端口、监听地址、HTTPS Cookie 策略、数据目录和初始化管理员密码仍由配置文件控制。页面设置不会改写 YAML。连接测试仅验证对应 API，不等于验证 docker push 或部署写权限。

见 `config.example.yaml` 注释。环境变量如下：

| 环境变量 | 说明 |
| --- | --- |
| `SWR_ADDRESS` / `SWR_ORGANIZATION` | 仓库地址 / 默认组织 |
| `SWR_USERNAME` / `SWR_PASSWORD` | docker login 用户名 / 登录密钥 |
| `SWR_AK` / `SWR_SK` / `SWR_PROJECT` | AK/SK 方式 |
| `BUILDER_ADMIN_PASSWORD` / `BUILDER_SECRET_KEY` | 首次管理员密码 / 机密配置加密主密钥 |
| `BUILDER_CONFIG` / `BUILDER_DATA_DIR` / `BUILDER_DOCKER_BIN` / `BUILDER_PORT` | 配置文件 / 数据目录 / docker 路径 / 端口 |
| `BUILDER_DATABASE_URL` / `BUILDER_DATABASE_PASSWORD` / `BUILDER_DATABASE_DRIVER` | 数据库连接串 / 口令（不要写进连接串）/ 驱动（`psycopg` 或 `gaussdb`） |

数据保存在 PostgreSQL / GaussDB 中。数据目录（默认 `./data`）：`jobs/<任务ID>/build.log`、`docker-config/`（平台独立的 docker 登录信息）、`deployments/<ID>/deploy.log`、`secret.key`（敏感配置加密主密钥）。

**必须一起备份数据库（`pg_dump -Fc`）与 secret.key**；配置集中的敏感值、证书文件与集群凭据都用 secret.key 加密，缺一不能恢复。使用 `BUILDER_SECRET_KEY` 时需单独安全托管该环境值。密钥丢失将无法解密页面保存的仓库/Kubernetes 凭证；本版没有在线密钥轮换功能。构建机或主密钥被攻破后，加密不能提供额外隔离。备份和数据目录应限制权限；`docker-config/` 同样含敏感登录信息，不要公开。

## 8. 常见问题

| 现象 | 处理 |
| --- | --- |
| `x509: certificate signed by unknown authority` | 把仓库 CA 放到 `/etc/docker/certs.d/<仓库地址>/ca.crt`；平台侧配置 `registry.ca_file` 或 `insecure: true` |
| `permission denied ... docker.sock` | 运行用户加入 docker 组：`usermod -aG docker swrbuilder`，重启服务 |
| `docker login 失败` / `SWR 鉴权失败` | 确认使用明文长期登录密钥或 AK/SK；先在构建机手动 `docker login` 验证 |
| `denied: requested access to the resource is denied` | 账号对组织没有推送权限，或组织名填写错误 |
| `架构不一致，已停止推送` | x86 构建机构建 arm64 镜像：应用的“构建平台”填 `linux/arm64` |
| 模板中有 `RUN` 且跨架构构建失败 | 需安装 qemu/binfmt（`docker run --privileged --rm tonistiigi/binfmt --install arm64`），或使用 arm64 构建机 |
| `max depth exceeded` | 层数超过 127：执行一次全量构建；可调小“累加层数上限” |
| 上传大文件 413 | 调大 Nginx `client_max_body_size` 和 `build.max_upload_mb` |

## 9. 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/info` | 平台、仓库、Docker 状态 |
| POST | `/api/registry/test` | 测试仓库连接与鉴权 |
| GET/POST | `/api/apps` | 应用列表 / 新建 |
| PUT/DELETE | `/api/apps/{id}` | 修改 / 删除应用 |
| POST | `/api/dockerfile/import` | 解析 Dockerfile |
| GET | `/api/apps/{id}/versions` | 版本列表、上一版本、建议版本 |
| POST | `/api/apps/{id}/preview` | 预览构建计划与 Dockerfile |
| POST | `/api/builds` | 上传文件并创建构建（multipart） |
| GET | `/api/jobs`、`/api/jobs/{id}`、`/api/jobs/{id}/log?offset=` | 构建记录（可按 `project_id`、`app_id`、`status` 筛选）、详情、增量日志 |
| POST | `/api/jobs/{id}/cancel` | 取消构建 |
| GET | `/api/overview` | 工作台：可见范围内的汇总与待我审批的发布 |
| GET/POST | `/api/projects`；PUT/DELETE `/api/projects/{id}` | 项目；`PUT/DELETE /api/projects/{id}/namespaces/{nid}` 分配命名空间 |
| GET/POST | `/api/services`；`/api/services/{id}/deployments`、`/rollback`、`/restart`、`/adopt` | 服务与发布（需 `Idempotency-Key` 请求头）；`adopt` 为导入服务的接管发布 |
| GET | `/api/service-templates`；POST `/api/services/preview`、`/api/services/{id}/render` | 服务模板；托管服务的清单预览与服务端校验（`dry_run`） |
| GET | `/api/services/{id}/runtime`、`/manifest`、`/effective-config`；POST `/api/services/status` | 运行状态（Pod、事件、访问地址）、发布快照、生效配置；列表的运行状态 |
| GET/PUT | `/api/environments`、`/api/environments/{id}`、`/profile`、`/effective-config`、`/compare` | 环境、环境档案、生效配置与环境对比 |
| POST | `/api/environments/{id}/import/analyze`、`/import/apply` | 导入已有服务：分析 kubectl 导出或集群对象 / 确认写入平台 |
| GET/POST | `/api/config-sets`、`/api/certificates`、`/api/ingress-profiles` | 分层配置集（版本、差异、查看明文）、证书库、负载均衡入口 |
| GET | `/api/deployments`、`/api/deployments/{id}`；POST `/api/deployments/{id}/approve`、`/reject` | 发布记录与审批 |
| GET/POST | `/api/users`；GET/PUT/DELETE `/api/users/{id}` | 账号（创建时可带 `grants` 初始授权） |
| GET/POST | `/api/bindings`；DELETE `/api/bindings/{id}`；GET `/api/bindings/options` | 授权与可授予的范围、角色 |
| GET/POST | `/api/role-definitions`；PUT/DELETE `/api/role-definitions/{code}` | 角色目录与权限目录、自定义角色 |

约定：列表接口返回 `{"items": [...], "total": n}`；删除成功返回 204；错误响应为 `{"detail": "中文说明", "code": "机器可读代码"}`，重复数据返回 409，无权访问的具体资源按不存在返回 404。

浏览器使用 HttpOnly / SameSite=Lax 会话 Cookie；修改请求额外要求 `X-Requested-With: XMLHttpRequest`。CI 请在“我的账号 → API 令牌”生成限时令牌（仅显示一次），通过环境变量传给调用进程，不在命令行明文写密码；令牌继承所属账号角色。改密、重置密码或停用账号会撤销相关令牌。

CI 调用示例（建议使用 HTTPS）：

```bash
curl -H "Authorization: Bearer $BUILDER_API_TOKEN" -F app_id=1 -F mode=incremental -F operator=jenkins \
  -F files=@target/cnnc-coding-fin-1.1.1-SNAPSHOT.jar https://<平台>/api/builds
```

## 10. 账号权限

账号的权限只来自**授权**：在全局、某个项目或某个命名空间范围授予一个角色。新账号默认没有任何权限。项目授权不会带来命名空间权限，反之亦然。

| 范围 | 内置角色 |
| --- | --- |
| 全局 | 平台管理员（全部权限）、审计员（全局只读与审计）、发布审批员（审批所有非生产环境的发布） |
| 项目 | 项目管理员（成员、应用、构建）、开发者（维护应用、构建、取消自己的构建）、项目只读 |
| 命名空间 | 命名空间管理员（成员、服务配置、发布、回滚、密钥）、环境审批员、部署员（配置并发布服务）、命名空间只读 |

- 也可在“访问控制 › 角色”按需组合权限，新建自定义角色。
- 全局角色只有平台管理员能授予；项目或命名空间的成员管理员只能在自己的范围内授予不超过自身权限、且不含成员管理权限的角色。
- 账号管理员只能管理全局权限不超过自己的账号。至少保留一名启用的平台管理员。
- 发起人不能审批自己的发布；生产环境必须由平台管理员确认；命名空间管理员不含审批权限。
- 用户组、SSO、机器人账号、CCE 证书自动签发尚未完成。

完整的权限点、角色、授权规则与扩展方法见 [账号与权限设计](docs/账号与权限设计.md)。

## 11. 部署模块

- 发布流程：在“集群与命名空间”纳管集群和命名空间并分配给项目 → 在环境中维护环境档案与环境层配置 → 新建服务（托管：按模板生成全部对象；纳管：关联已有 Deployment）→ 在“发布中心”或服务详情选择成功构建发布 → 按命名空间策略审批 → worker 下发并等待滚动完成。
- 托管服务：提交发布时记录所用配置的版本快照，审批人批准的就是将要下发的内容；worker 先对全部对象做服务端 dry-run，再用 Server-Side Apply（`fieldManager=swr-builder`）下发，只管理平台声明的字段。配置变化通过 Pod 模板注解 `swr-builder/config-hash` 触发滚动。回滚与重新发布按目标发布的快照原样下发。
- 已有服务可以在环境中“导入已有服务”：上传 `kubectl get deploy,svc,ing,cm,secret,hpa,pod -o yaml` 的导出或直接读取集群，平台推断环境档案和环境层配置并列出不一致，确认后写入平台；之后用“接管发布”（沿用 Deployment 当前的镜像）由平台接管。
- 旧部署目标（**Webhook** 或 **CCE / Kubernetes**，v1.2 起不再执行发布）只保留 `/api/deploy/targets` 接口供迁移核对与连接测试，控制台不再提供入口。SSH / Compose 仅为注册表占位，不可执行。
- Webhook 固定 POST JSON：`event`、`deployment_id`、`image`、`app`、`job_id`、`tag`、`digest`、`operator`。可配置请求头、TLS 校验及 HMAC-SHA256 签名；`X-SWR-Signature: sha256=<十六进制签名>` 对原始 UTF-8 请求体计算。接收方应按 `deployment_id` 去重，建立自身的防重放/鉴权机制。
- Webhook 测试会发送 `event=ping`，请确保接收端不会误触发部署。HTTP 2xx 仅表示网关接受请求，不代表应用已就绪；异步回调确认尚未实现。
- 集群凭据以密文保存，支持标准 kubeconfig Config 中的静态 Token 或内嵌客户端证书/私钥；也可在“集群与命名空间”中直接上传 PEM 客户端证书、私钥和可选 CA。可纳管已有 namespace、执行 SSAR 权限检查和生成供集群管理员审核的 RBAC 模板。kubeconfig `exec`、`auth-provider` 和外部文件凭据不受支持且不会执行/读取。生产发布只有平台管理员可批准；非生产环境由全局发布审批员角色批准。
- 服务配置支持不可变 revision 与 CAS 更新。纳管服务（`image_only`）只更新镜像；托管服务（`managed`）下发完整清单。
- 服务发布写入数据库队列，使用幂等键、租约和 fencing token。工作进程重启时，执行中的 Kubernetes 写操作标记 `unknown`，须先核对集群状态；不会盲目重放。
- 生产 namespace 始终要求平台管理员审批；审批人与发起人分离。非生产 namespace 可要求普通审批或额外启用管理员审批。回滚、重启、扩缩容请求已有状态与权限入口，仍需扩展测试集群覆盖和操作体验。
- Namespace 可配置冻结截止时间及部署窗口；窗口使用 IANA 时区和 ISO 星期编号（周一 1 至周日 7），跨午夜窗口归属开始日。API 入队和 worker 执行前都会检查当前策略。
- 服务发布只能引用所属应用的成功构建。Deployment 中写入构建的版本号（`仓库:版本号`），不写 digest；构建时的 digest 记在发布单上，worker 写入集群前核对该版本号仍指向它（版本号在平台之外被覆盖或删除时停止发布）。旧 `/api/deployments` 手工发布接口返回 HTTP 410；旧自动部署事件不执行，以免绕过 namespace 审批。
- Webhook / Kubernetes 旧目标保留为迁移查看和管理员连接测试用途。发布请创建服务并使用 namespace 授权与审批队列。
- 当前不提供灰度发布、自动回滚、CCE 证书轮换、配置变更的批量发布或生产 SLA 保证。真实 CCE/SWR/Kubernetes 联调、灾备演练和审批业务验收尚未完成。

旧部署适配器（`deploy/base.py` 的 `Deployer`，在 `deploy/__init__.py` 注册）只服务于旧部署目标的迁移核对与连接测试，服务发布不经过适配器。通过 `events.py` 订阅构建/部署事件扩展通知、扫描等模块。

## 12. 云端依赖

Kubernetes 部署直接使用“集群与命名空间”中导入的 kubeconfig，不需要华为云 Stack 的 IAM、CCE 或 SWR 管理 API 配置。HCS 后端代码仅作为可选兼容模块保留，不参与镜像构建或 Kubernetes 发布；系统设置界面不展示 HCS 配置。

## 13. 测试

```bash
pip install -r requirements-dev.txt
# 测试需要一个有 CREATEDB 权限的 PostgreSQL 账号，每个测试新建并删除自己的数据库
export SWR_TEST_DATABASE_URL=postgresql://postgres:<口令>@127.0.0.1:5432/postgres
pytest -q        # 使用模拟 SWR、假 docker 与模拟的 Kubernetes API，不需要真实仓库和集群
(cd web && npm run typecheck && npm run build)   # 前端类型检查与构建
```

CI 在 PostgreSQL 16 与 openGauss 5.0（PG 兼容模式，代表 GaussDB）上各运行一遍后端测试。

文档：

| 文档 | 内容 |
| --- | --- |
| [docs/系统架构.md](docs/系统架构.md) | 运行时结构、代码分层、数据与安全架构、部署架构 |
| [docs/系统设计.md](docs/系统设计.md) | 业务规则：构建、环境与配置、服务、发布、导入、运行状态、状态机 |
| [docs/开发文档.md](docs/开发文档.md) | 开发约定、模块职责、迁移、测试方法 |
| [DESIGN.md](DESIGN.md) | 前端设计规范：布局、组件、状态、文案、响应式与深色模式 |
| [docs/账号与权限设计.md](docs/账号与权限设计.md) | 权限模型与授予规则 |
| [docs/v2-服务创建与环境配置设计.md](docs/v2-服务创建与环境配置设计.md) | v2 的方案比较与决定记录 |
| [docs/ARM64离线部署与更新.md](docs/ARM64离线部署与更新.md) | ARM64 全量离线包、补丁包（代码 / DDL / DML）、升级、备份恢复、v1 迁移 |
| [AGENTS.md](AGENTS.md) / [CLAUDE.md](CLAUDE.md) | 编码代理（Codex / Claude Code）的协作规则 |

v1.3 的前端与后端分层重构记录见 `docs/重构设计-Vue3与业务逻辑.md`。


## 14. 升级与上线检查

1. 停止旧服务，备份整个 data 目录（含 `builder.db` 与 `secret.key`）与配置文件。
2. 从 v1.x 升级到 v2：准备一个空的 PostgreSQL / GaussDB 数据库，执行 `python -m swr_builder db import-sqlite --from data/builder.db -c config.yaml`。命令在副本上把 SQLite 升级到 v1.3 结构后整体导入，保留原 ID 并核对每张表的行数，不修改原文件；必须使用同一个 `secret.key`。用离线包部署的 v1，按[部署文档第 9 节](docs/ARM64离线部署与更新.md#9-从-v1sqlite迁移)安装到新目录并用 `SWR_IMPORT_V1` 导入。
3. 验证登录、强制改密和权限，再分配开发者/只读账号；删除旧配置里不再需要的明文 `server.users`。已有账号不会被初始化配置反复覆盖。
4. 在测试仓库验证真实 Docker pull/build/push，确认构建机架构和磁盘空间。
5. 私有云认证、网络、CA、Kubernetes 权限与发布就绪状态需要现场联调；模拟测试不代替这些验收。
6. 配置 HTTPS、强 Cookie、网络白名单及备份，保护 Docker socket。可访问 Docker 的平台进程等价于构建机 root 权限，应运行在专用构建机上。
7. 旧构建与部署目标仍为进程内调度，重启后中断并标失败；服务发布 worker 使用持久化队列，处理中断标记为 `unknown`，人工核对 Kubernetes 实际状态后再操作。

真实云端验收（含托管服务在 CCE 上的接管与下发）、用户组/SSO、CCE 证书轮换、审计外送、监控告警、备份恢复演练及业务签署仍是生产上线前的工作。
