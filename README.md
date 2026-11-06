# SWR 镜像构建平台

面向华为云 Stack SWR 的轻量级镜像编译平台：**页面上传制品 → 自动获取上一版本 → 基于上一版本累加构建 → 推送到 SWR**，全程可视化日志，构建记录可追溯。

- 自动识别上一版本（按版本号或推送时间），自动生成新版本号（`1.0.7 → 1.0.8`、`1.0.9-arm64 → 1.0.10-arm64`）
- **累加构建**：`FROM 上一版本` + `COPY 本次文件`，只新增 1 层，推送快、节省存储
- **全量构建**：基于基础镜像 + 应用模板（可一键导入现有 Dockerfile）
- 层数保护：上一版本层数达到上限（默认 100，Docker 硬上限 127）时自动改为全量构建
- 架构保护：构建结果与目标架构（如 arm64）不一致时停止推送
- 不覆盖已有版本、同一仓库构建串行、实时日志、取消任务、平台账号登录

## 1. 环境要求

| 项目 | 要求 |
| --- | --- |
| 构建机 | Linux，已安装 Docker（建议 20.10+），能访问 SWR 地址 |
| Python | 3.11+（使用容器部署时不需要） |
| 架构 | 镜像是 arm64：构建机最好是 arm64；x86 构建机需在应用中设置“构建平台 linux/arm64” |

## 2. 快速开始（源码方式）

```bash
cd swr-image-builder
python3 -m venv venv && . venv/bin/activate
pip install -r requirements.txt

# 构建前端（Vue 3，需要 Node 18.18+；产物输出到 swr_builder/webui/）
(cd web && npm install && npm run build)   # 有 package-lock.json 时用 npm ci

cp config.example.yaml config.yaml        # 修改仓库地址、组织、用户名、平台登录账号
export SWR_USERNAME='ndc302-region-1_his_beta@<AK>'
export SWR_PASSWORD='<长期有效登录密钥>'    # 不要写进代码仓库

python -m swr_builder -c config.yaml
```

浏览器打开 `http://<构建机IP>:8080`，进入独立登录页。未构建前端时页面会提示构建命令，API 不受影响。

前端开发：先按上面启动后端，再执行 `cd web && npm run dev`，浏览器打开 `http://localhost:5173`（`/api` 自动转发到 8080）。构建机不方便安装 Node 时，可以在其他机器构建后把 `swr_builder/webui/` 一起拷贝过去，或直接使用容器部署。

- 首次启动管理员为 **admin**。`server.admin_password` / `BUILDER_ADMIN_PASSWORD` 为空时，自动生成初始密码并写入 `data/admin-initial-password.txt`（权限 0600），不会输出到日志。首次登录必须改密；改密后文件自动删除。
- 如指定初始化密码，请使用强密码；该配置只用于首次创建账号，不是后续密码重置机制。
- 原 v1.0 的 `server.users` 会在空账号库首次启动时迁移为管理员，并要求改密。
- 生产务必使用 HTTPS，配置 `server.secure_cookie: true`，限制访问来源；平台没有“免登录”运行模式。
- 管理员在“账号管理”创建开发者或只读账号；“我的账号”支持资料、密码、登录设备和 API 令牌。

> 构建 worker 与旧版部署目标仍限定单进程运行。v1.2 服务发布队列使用 SQLite 持久化与租约；跨主机高可用尚未实现。

## 3. 部署方式

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
docker build -t swr-builder:1.3.0-dev.1 .     # 多阶段构建：先用 Node 构建前端，运行时镜像不含 Node
# 内网环境可指定镜像源：--build-arg NPM_REGISTRY=... --build-arg PIP_INDEX_URL=...
docker run -d --name swr-builder --restart unless-stopped -p 8080:8080 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /opt/swr-builder/config.yaml:/app/config.yaml:ro \
  -v /opt/swr-builder/data:/app/data \
  -e SWR_USERNAME='ndc302-region-1_his_beta@<AK>' -e SWR_PASSWORD='<登录密钥>' \
  swr-builder:1.3.0-dev.1
```

也可以使用 `deploy/docker-compose.yml`。容器内只有 docker CLI，实际构建由宿主机 Docker 完成。

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

1. **应用管理 → 新建应用**：粘贴现有 Dockerfile 点“导入”，自动识别基础镜像、工作目录、目标文件名、属主（`--chown`）；填写镜像名（如 `cnnc-coding-chc-fin`）和构建平台（如 `linux/arm64`）。
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

管理员可在“系统设置”维护仓库、构建、安全、站点名称与 HCS 连接参数。优先级为 **环境变量 > 数据库中的页面设置 > config.yaml / 默认值**。环境变量控制的项在页面只读；敏感值不回显，留空保持，明确勾选才清空。设置生效不需要重启；有未结束的构建时不能修改仓库及构建参数。

端口、监听地址、HTTPS Cookie 策略、数据目录和初始化管理员密码仍由配置文件控制。页面设置不会改写 YAML。连接测试仅验证对应 API，不等于验证 docker push 或部署写权限。

见 `config.example.yaml` 注释。环境变量如下：

| 环境变量 | 说明 |
| --- | --- |
| `SWR_ADDRESS` / `SWR_ORGANIZATION` | 仓库地址 / 默认组织 |
| `SWR_USERNAME` / `SWR_PASSWORD` | docker login 用户名 / 登录密钥 |
| `SWR_AK` / `SWR_SK` / `SWR_PROJECT` | AK/SK 方式 |
| `BUILDER_ADMIN_PASSWORD` / `BUILDER_SECRET_KEY` | 首次管理员密码 / 机密配置加密主密钥 |
| `HCS_IAM_ENDPOINT` / `HCS_AK` / `HCS_SK` | 私有云 IAM Endpoint / 专用 AK / SK |
| `HCS_PROJECT_ID` / `HCS_DOMAIN_ID` / `HCS_SECURITY_TOKEN` | 作用域 ID / 临时凭证 Security Token |
| `HCS_PASSWORD` / `HCS_TOKEN` | 可选密码 / 预置 Token 模式 |
| `BUILDER_CONFIG` / `BUILDER_DATA_DIR` / `BUILDER_DOCKER_BIN` / `BUILDER_PORT` | 配置文件 / 数据目录 / docker 路径 / 端口 |

数据目录（默认 `./data`）：`builder.db`（SQLite）、`jobs/<任务ID>/build.log`、`docker-config/`（平台独立的 docker 登录信息）、`deployments/<ID>/deploy.log`、`secret.key`（敏感配置加密主密钥）。

**必须一起备份 builder.db 与 secret.key**；使用 `BUILDER_SECRET_KEY` 时需单独安全托管该环境值。密钥丢失将无法解密页面保存的仓库/部署/HCS 凭证；本版没有在线密钥轮换功能。构建机或主密钥被攻破后，加密不能提供额外隔离。备份和数据目录应限制权限；`docker-config/` 同样含敏感登录信息，不要公开。

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
| GET | `/api/jobs`、`/api/jobs/{id}`、`/api/jobs/{id}/log?offset=` | 构建记录、详情、增量日志 |
| POST | `/api/jobs/{id}/cancel` | 取消构建 |

浏览器使用 HttpOnly / SameSite=Lax 会话 Cookie；修改请求额外要求 `X-Requested-With: XMLHttpRequest`。CI 请在“我的账号 → API 令牌”生成限时令牌（仅显示一次），通过环境变量传给调用进程，不在命令行明文写密码；令牌继承所属账号角色。改密、重置密码或停用账号会撤销相关令牌。

CI 调用示例（建议使用 HTTPS）：

```bash
curl -H "Authorization: Bearer $BUILDER_API_TOKEN" -F app_id=1 -F mode=incremental -F operator=jenkins \
  -F files=@target/cnnc-coding-fin-1.1.1-SNAPSHOT.jar https://<平台>/api/builds
```

## 10. 账号权限

| 角色 | 权限 |
| --- | --- |
| 管理员 | 平台全局管理、应用、项目、集群、命名空间、服务、审批、设置和审计 |
| 开发者 | 通过项目绑定查看应用、构建和任务；拥有项目权限时可构建/配置，部署另需 namespace 角色 |
| 只读用户 | 旧版默认项目迁移为只读；新授权使用 `project_viewer` / `namespace_viewer` |

v1.2 开始按项目与命名空间 role binding 做服务端行级授权。项目绑定不会自动授予 namespace 权限；生产发布根据 namespace policy 进入审批。用户组、SSO、机器人账号、CCE 证书自动签发尚未完成。至少保留一名平台管理员。

## 11. 部署模块

- “部署中心”配置目标：**Webhook** 或 **CCE / Kubernetes**。SSH / Compose 仅为注册表占位，不可执行。
- Webhook 固定 POST JSON：`event`、`deployment_id`、`image`、`app`、`job_id`、`tag`、`digest`、`operator`。可配置请求头、TLS 校验及 HMAC-SHA256 签名；`X-SWR-Signature: sha256=<十六进制签名>` 对原始 UTF-8 请求体计算。接收方应按 `deployment_id` 去重，建立自身的防重放/鉴权机制。
- Webhook 测试会发送 `event=ping`，请确保接收端不会误触发部署。HTTP 2xx 仅表示网关接受请求，不代表应用已就绪；异步回调确认尚未实现。
- 集群凭据以密文保存，当前接入方式为 SA Token；可纳管已有 namespace、执行 SSAR 权限检查和生成供管理员审核的 RBAC 模板。CCE 证书签发/撤销仍待现场适配。
- 服务配置支持不可变 revision、CAS 更新和只写密钥。worker 当前可对 `image_only` 服务执行固定 digest 的 Deployment 镜像更新并等待滚动就绪；`managed` 模式的完整清单 apply 尚未启用。
- 服务发布写入 SQLite 队列，使用幂等键、租约和 fencing token。工作进程重启时，执行中的 Kubernetes 写操作标记 `unknown`，须先核对集群状态；不会盲目重放。
- 生产 namespace 默认要求审批；审批人与发起人分离。回滚、重启、扩缩容请求已有状态与权限入口，仍需扩展测试集群覆盖和操作体验。
- Namespace 可配置冻结截止时间及部署窗口；窗口使用 IANA 时区和 ISO 星期编号（周一 1 至周日 7），跨午夜窗口归属开始日。API 入队和 worker 执行前都会检查当前策略。
- v1.2 服务发布只能引用所属应用的成功构建，并固定到 digest。旧 `/api/deployments` 手工发布接口返回 HTTP 410；旧自动部署事件不执行，以免绕过 namespace 审批。
- Webhook / Kubernetes 旧目标保留为迁移查看和管理员连接测试用途。发布请创建服务并使用 namespace 授权与审批队列。
- 当前不提供灰度发布、自动回滚、CCE 证书轮换、managed 资源 SSA 或生产 SLA 保证。真实 CCE/SWR/Kubernetes 联调、灾备演练和审批业务验收尚未完成。

新增适配器：继承 `deploy/base.py` 的 `Deployer`，实现 `validate / test / deploy`，在 `deploy/__init__.py` 注册；前端从字段描述生成表单。通过 `events.py` 订阅构建/部署事件扩展通知、扫描等模块。

## 12. 华为云 Stack 8.5.1 后端接入

已实现独立 HCS 连接层，默认使用 **AK/SK 签名调用 POST /v3/auth/tokens**；以华为官方 SDK 计算签名，获取 X-Subject-Token，校验有效期与作用域并缓存。密码和预置 Token 是显式选择的可选模式，认证失败不自动回退。

- 如果升级前已在数据库保存 password/token 模式，升级不会擅自更换；请在页面显式选择 aksk 后保存。
- 系统设置 → 华为云 Stack 后端：填写 IAM Endpoint、AK/SK、project/domain 作用域及对应 ID，然后测试认证。
- 可使用 `HCS_AK`、`HCS_SK`、`HCS_SECURITY_TOKEN`、`HCS_PROJECT_ID`、`HCS_DOMAIN_ID`；页面秘密值加密、不回显。
- 默认空请求体 + 已签名的 X-Project-Id / X-Domain-Id；如现场网关要求 JSON，显式配置 `aksk_request_body`。不推测 identity 方法，也不会盲试或静默改用密码。
- 使用官方标准 SDK-HMAC-SHA256 / Access 签名。某些网关旧式 Credential 签名不在本版实现范围；实际网关兼容性需要现场验证。
- 临时 AK/SK 可带 X-Security-Token；SK 不作为密码、请求头或请求体发送。
- `python -m swr_builder.cloud -c config.yaml` 可执行认证诊断，结果不输出 Token/AK/SK。
- 已通过模拟 IAM 与官方 SDK 固定向量测试，但**没有连接用户真实 Stack 环境**。
- SWR Registry、HCS IAM、Kubernetes 使用独立凭证。SWR/CCE/ECS 的管理面业务 API、ManageOne SSO 仍需按现场组件版本开发。
- 详细协议、配置、JSON 模板及故障排查见 `docs/华为云Stack8.5.1接口接入设计.md`。

## 13. 测试

```bash
pip install -r requirements-dev.txt
pytest -q        # 使用模拟 SWR + 假 docker，不需要真实仓库
(cd web && npm run typecheck && npm run build)   # 前端类型检查与构建
```

详细设计见 `docs/系统设计.md`；v1.3 的前端与后端分层见 `docs/重构设计-Vue3与业务逻辑.md`。


## 14. 升级与上线检查

1. 停止 v1.0 服务，备份整个 data 目录与配置文件。
2. 使用 v1.2 代码安装依赖；保留原 `data/builder.db`，启动时通过 checksum 记录执行 SQLite 迁移，将原数据回填到默认项目并迁移角色绑定。升级前必须备份并验证数据库、构建上下文和 `secret.key`。
3. 验证登录、强制改密和权限，再分配开发者/只读账号；删除旧配置里不再需要的明文 `server.users`。已有账号不会被初始化配置反复覆盖。
4. 在测试仓库验证真实 Docker pull/build/push，确认构建机架构和磁盘空间。
5. 私有云认证、网络、CA、Kubernetes 权限与发布就绪状态需要现场联调；模拟测试不代替这些验收。
6. 配置 HTTPS、强 Cookie、网络白名单及备份，保护 Docker socket。可访问 Docker 的平台进程等价于构建机 root 权限，应运行在专用构建机上。
7. 旧构建与部署目标仍为进程内调度，重启后中断并标失败；服务发布 worker 使用持久化队列，处理中断标记为 `unknown`，人工核对 Kubernetes 实际状态后再操作。

真实云端验收、managed apply、用户组/SSO、CCE 证书轮换、审计外送、监控告警、备份恢复演练及业务签署仍是生产上线前的工作。
