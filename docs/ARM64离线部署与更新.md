# ARM64 离线部署与代码更新

适用架构：Linux aarch64 / arm64。默认部署目录 `/data/swr`，访问端口 **8082**，容器内部端口 8080。

此交付方式将运行依赖固定在镜像中，业务代码与编译后的页面按版本挂载。正常更新不需要重新构建或传输镜像，不需要在目标主机安装 Python、Node、npm 或联网安装依赖。

## 1. 交付物

完整包命名为 `swr-builder-arm64-<版本>.tar.gz`，旁边的 `.sha256` 文件用于检查传输完整性。解压后包括：

| 路径 | 用途 |
| --- | --- |
| `images/swr-builder-arm64.tar.gz` | `docker save` 导出的完整 ARM64 镜像，含全部基础层、Python 依赖、默认代码和前端构建工具 |
| `releases/swr-code-<版本>.tar.gz` | Python 业务代码和已编译前端，含逐文件 SHA256 与运行依赖标识 |
| `source.tar.gz` | 当前交付版本完整开发源码，包含本次工作区改动；不含生产配置、数据库、凭证或 node_modules |
| `dependencies/requirements.lock` | 所有 Python 依赖的固定版本和 wheel 哈希 |
| `dependencies/wheelhouse/` | ARM64 / 纯 Python 离线 wheel，镜像内也保留一份 |
| `tools/docker-28.5.2-aarch64.tgz` | 可选 Docker Engine 静态离线安装包 |
| `tools/install-docker.sh` | 仅用于没有 Docker 的 ARM64 systemd 主机，不会覆盖已有安装 |
| `install.sh` / `upgrade.sh` | 首次安装 / 运行依赖变化时升级完整包 |
| `swrctl` / `ops/` | 启停、状态、日志、备份、代码打包、更新、回滚、恢复工具 |
| `config.yaml` / `swr-builder.env.example` | 无现场地址和业务数据的配置模板 |
| `SHA256SUMS` / `manifest.json` | 包内文件校验、镜像 ID、依赖标识、源代码基准和基础镜像摘要 |

本包包含平台本身及其依赖。用于业务镜像构建的 Java/JRE 等基础镜像取决于现场应用，应另行导入目标 Docker 或由内网 SWR 提供；本包不包含任何现场 SWR 镜像、账号或证书。

集群连接支持标准单文件 kubeconfig `kind: Config`，按 `current-context` 读取静态 Token 或内嵌客户端证书/私钥，并将有效凭据加密保存；系统设置中的 “CCE / Kubernetes” 集群表单支持直接粘贴 YAML。不会执行 `exec` / `auth-provider`，不会读取 `tokenFile` 或本机证书路径。需要证书文件的配置请在可信环境中转换成 `client-certificate-data` / `client-key-data` 再导入。

## 2. 目标主机准备

1. Linux ARM64 主机，内核支持 Docker 所需的 cgroups、namespace 和 overlayfs。
2. 已运行的 Docker Engine；建议使用本包验证配套的 Docker 28.5.2。运维账号需要 Docker socket 权限，以下示例使用 root。
3. `/data/swr` 至少预留 5 GB 用于平台、源码和解包临时空间。业务构建上下文、基础镜像、日志和历史备份需要额外空间，按业务规模规划。
4. 确认 8082 未被占用，防火墙允许预期客户端访问；部署前完成现场 SWR / Kubernetes 内网连通性和证书准备。Kubernetes 部署不需要 HCS 配置。
5. 平台必须单实例运行。不要将同一数据目录同时挂给两个平台容器。

检查：

```sh
uname -m
docker version
docker info --format '{{.Architecture}}'
df -h /data
ss -lnt | grep ':8082 '
```

输出架构应为 `aarch64` 或 `arm64`。不需要在 ARM64 目标机安装 QEMU。`SWR_ALLOW_EMULATION=1` 只供 x86 构建机的隔离验收使用，不应在目标机绕过架构检查。

### 2.1 主机尚未安装 Docker

先检查完整包哈希并解压，再执行：

```sh
sudo sh tools/install-docker.sh
```

脚本要求 root、systemd 和 iptables，检测到现有 Docker 或已有 docker.service 会停止，不会替换它们。内核和发行版组件不属于通用镜像包；如缺少 iptables，应从目标 Linux 发行版的离线 RPM/DEB 介质补齐后再运行。不会通过关闭 Docker 防火墙来规避此要求。已有 Docker 的主机跳过本步骤。

## 3. 首次安装

将完整包及对应 `.sha256` 文件拷到目标主机同一目录。下例使用本次交付版本 `20261109-r1`：

```sh
sha256sum -c swr-builder-arm64-20261109-r1.tar.gz.sha256
sudo mkdir -p /data/swr/packages
sudo tar -xzf swr-builder-arm64-20261109-r1.tar.gz -C /data/swr/packages
cd /data/swr/packages/swr-builder-arm64-20261109-r1
sudo sh install.sh /data/swr 8082
sudo /data/swr/swrctl status
sudo /data/swr/swrctl verify
```

安装会校验包内 SHA256、导入完整镜像、检查镜像架构、创建配置和数据目录，再启动容器并等待健康检查。首次管理员用户名为 `admin`，初始随机密码只在目标主机读取：

```sh
sudo cat /data/swr/data/admin-initial-password.txt
```

访问 `http://<目标主机IP>:8082`，按页面提示修改初始密码，然后配置仓库、项目、Kubernetes 集群与命名空间。不要将初始密码、环境文件或数据备份发给无关人员。

默认容器名是 `swr-builder`。如需要指定其他容器名或只监听特定地址，可在首次安装时使用：

```sh
sudo env SWR_CONTAINER_NAME=swr-builder SWR_BIND_ADDRESS=0.0.0.0 \
  sh install.sh /data/swr 8082
```

安装不会覆盖现有部署、已有数据或配置。若安装因端口占用而启动失败，先释放该端口，再执行 `/data/swr/swrctl start`；不要重复覆盖安装。正常安装不执行 `pip install`、`npm install` 或外网镜像拉取。

## 4. 目录结构与配置

```text
/data/swr/
  swrctl                      管理入口
  deploy.json                 当前版本、镜像 ID、端口、容器名
  runtime-image               管理工具使用的固定镜像 ID
  current -> releases/<版本>  当前业务代码
  releases/<版本>/            不可变代码和前端产物
  source/                     初次交付源码，供后续离线编辑
  source-versions/<版本>/     完整依赖升级包附带的新版源码
  updates/                    小体积代码更新包
  config/config.yaml          现场配置，容器内 /etc/swr/config.yaml
  config/swr-builder.env      敏感环境变量（600 权限）
  config/certs/               现场 CA / 客户端证书
  data/                       数据库、密钥、任务、上传、Docker 登录信息
  backups/                    停机一致备份及其校验文件
  ops/                        控制器源码
  packages/                   可选：保留完整交付包
```

容器使用 `/var/lib/swr` 作为持久数据目录，使用宿主机 `/var/run/docker.sock` 构建与推送业务镜像。控制器始终重建容器并明确挂载对应版本目录，避免切换软链接后容器仍使用旧代码。数据和配置不会随代码目录切换。

修改 `/data/swr/config/config.yaml` 或 `swr-builder.env` 后执行：

```sh
sudo /data/swr/swrctl restart
```

仓库和 Kubernetes 页面设置可直接在系统设置中保存，无需重启。配置优先级仍是 **环境变量 > 数据库页面设置 > YAML/默认值**。管理工具固定容器内部端口和数据路径，请不要用页面或环境文件改变它们。

自签名 CA 可放在 `/data/swr/config/certs`，配置中的路径填写 `/etc/swr/certs/<文件名>`。Docker daemon 访问仓库时还需在宿主机 `/etc/docker/certs.d/<仓库地址>/ca.crt` 安装 CA；平台的 Registry API TLS 设置不影响 Docker daemon。如果仓库证书 SAN 与访问地址不匹配，安装 CA 无法修复名称校验，需修复仓库证书，或仅在测试环境将地址加入 Docker daemon `insecure-registries` 并重启 Docker。编辑 `/etc/docker/daemon.json` 时先保留并合并现有配置；重启 daemon 会影响该主机上的容器，平台不会自动执行。对外接入 HTTPS 后将 `server.secure_cookie` 设为 `true`。

```json
{
  "insecure-registries": ["<仓库主机名>"]
}
```

Docker 官方建议优先使用名称匹配且由受信任 CA 签发的证书；`insecure-registries` 会降低该仓库的 TLS 保护。[Docker daemon insecure registry 配置](https://docs.docker.com/reference/cli/dockerd/) [Docker 仓库证书](https://docs.docker.com/engine/security/certificates/)

## 5. 后续更新：仅改代码，不换镜像

### 5.1 在目标主机离线修改并打包

编辑 `/data/swr/source/swr_builder/` 中的 Python 代码或 `/data/swr/source/web/src/` 中的 Vue 页面，然后：

```sh
sudo /data/swr/swrctl build-update /data/swr/source 20261110-r1
sudo /data/swr/swrctl update /data/swr/updates/swr-code-20261110-r1.tar.gz
sudo /data/swr/swrctl status
sudo /data/swr/swrctl verify
```

`build-update` 在已有 ARM64 镜像里使用预装 Node、TypeScript、Vue 编译工具构建前端，并生成完整代码包；管理容器关闭网络。它不修改正在运行的代码，不联网、不安装依赖，也不需要宿主机 Node/Python。

每次使用新的版本名。更新包包含完整后端和完整前端资源，避免零散覆盖遗留旧文件。不要直接编辑 `current` 或 `releases` 内文件，否则会破坏完整性校验。

### 5.2 从开发机交付代码包

可在一套相同完整镜像的开发/验收环境运行上述 `build-update`，将生成的 `swr-code-<版本>.tar.gz` 和 `.sha256` 拷到目标 `/data/swr/updates/`：

```sh
cd /data/swr/updates
sha256sum -c swr-code-20261110-r1.tar.gz.sha256
sudo /data/swr/swrctl update /data/swr/updates/swr-code-20261110-r1.tar.gz
```

控制器拒绝缺文件、哈希错误、重复版本不同内容、非法路径或与运行依赖不匹配的代码包。更新过程会检查未结束的构建/发布任务；有任务时拒绝更新，请先完成或在页面取消任务。安排维护窗口并暂停新任务提交，避免停机前仍有任务入队。

更新会短暂停服，依次执行停止、备份、切换代码、重建容器与健康检查。若新版本无法启动，会恢复更新前的代码、配置和数据。正常成功更新保留数据库、账号、设置、任务记录、上传文件和加密主密钥。

## 6. 预装依赖与未来扩展

本次包固定了 81 个 Python 直接/间接依赖，准确版本和哈希以 `dependencies/requirements.lock` 为准。

| 能力 | 已包含 |
| --- | --- |
| Web/API | FastAPI、Uvicorn、multipart、HTTPX、requests、websockets |
| YAML/JSON/配置 | PyYAML（`import yaml`）、ruamel.yaml、jsonschema、orjson、Jinja2、python-dotenv、pydantic-settings、TOML |
| 认证/加密/重试 | cryptography、PyJWT、tenacity |
| Kubernetes/SSH | kubernetes、paramiko |
| 华为云 SDK | core / IAM / SWR / CCE / ECS，SDK 版本固定为 3.1.219 |
| 时区/计划/数据库扩展 | tzdata、dateutil、croniter、SQLAlchemy、Alembic、email-validator |
| 前端开发 | ARM64 Node 22、npm、按 package-lock.json 安装的 Vue/Element Plus/TypeScript/Vite 等依赖 |
| 容器操作 | ARM64 Docker CLI、Buildx、Compose 插件 |

这些库可以供后续代码直接引用；预装 SDK 不代表对应新业务功能已经实现，也不代表现场云 API 已通过验收。

未来若引入此清单之外的新库、修改前端依赖锁或升级 Python/Node，需要在联网构建机生成一次新版完整包。普通业务和页面改动仍只发布代码包。无法预先保证所有未知第三方库都已包含。

联网构建机流程：

构建机需要 Docker、Python 3.11+ 和 Node 22。ARM64 原生构建机可以直接执行；x86 构建机可先安装本次验收使用的 ARM64 binfmt 处理器（只用于构建机，不在目标 ARM64 主机运行）：

```sh
docker run --privileged --rm \
  tonistiigi/binfmt@sha256:400a4873b838d1b89194d982c45e5fb3cda4593fbfd7e08a02e76b03b21166f0 \
  --install arm64
docker buildx inspect
```

```sh
# 使用源码中的 deploy/offline/requirements.in 维护 Python 直接依赖。
# 修改 Python 依赖后，将旧 lock 和 wheelhouse 移到备份目录，再重新解析。
# 前端依赖修改后同时更新 web/package-lock.json。
# 在 ARM64 构建机运行；x86 构建机需先配置 ARM64 QEMU/binfmt。
bash scripts/build-arm64-bundle.sh 20261120-r1
```

脚本固定基础镜像摘要，完整包保留所有 wheel，并在构建时离线安装且核验哈希。基础镜像需要升级时更新 `deploy/offline/base-images.json`，随后重新做完整验收。

目标机升级依赖使用新版完整包：

```sh
cd /data/swr/packages/swr-builder-arm64-20261120-r1
sudo sh upgrade.sh /data/swr
sudo /data/swr/swrctl verify
```

该流程保留现场数据、配置和旧运行镜像，升级前仍会备份。新的开发源码放在 `/data/swr/source-versions/20261120-r1`，避免覆盖原 `source/` 中尚未发布的修改；后续 `build-update` 使用新版源码目录。普通代码更新不更新管理工具本身，管理工具的改动通过完整包分发。

## 7. 备份、回滚与恢复

```sh
sudo /data/swr/swrctl backup
sudo /data/swr/swrctl rollback
# 也可指定 releases 中保留的版本
sudo /data/swr/swrctl rollback 20261109-r1
```

`backup` 在停止应用后打包整个 `data/` 和 `config/`，完成后恢复原运行状态。`rollback` 只切换代码及其对应运行镜像，保留当前业务数据，并在切换前再次备份。

`data/builder.db` 与 `data/secret.key` 必须一起保存；使用 `BUILDER_SECRET_KEY` 时还需保护包含该值的环境文件。备份包含敏感配置和仓库凭证，应按生产数据限制访问并复制到独立备份位置。

如果升级改变了数据库结构，旧代码可能不能读新结构。此时使用升级前的一致备份恢复：

```sh
sudo /data/swr/swrctl restore /data/swr/backups/backup-<时间>.tar.gz
```

恢复会把数据库、配置及代码版本一起恢复到备份时间，**备份之后的业务数据会被替换**。工具会先额外保存当前状态作为安全备份。保留与备份对应的 `releases/` 代码目录和运行镜像；不要在回滚窗口内执行镜像清理或删除旧版本。

## 8. 日常运维与验收

```sh
sudo /data/swr/swrctl status
sudo /data/swr/swrctl logs --tail 100
sudo /data/swr/swrctl verify
sudo /data/swr/swrctl stop
sudo /data/swr/swrctl start
curl -fsS http://127.0.0.1:8082/healthz
```

`verify` 检查当前代码的逐文件哈希、ARM64 架构、常用依赖导入、YAML、时区、Docker/Node 工具和容器健康状态。健康接口成功不等于已验证 SWR 推送或 Kubernetes 发布。

现场交付验收还应完成一次登录、配置保存、测试仓库的真实构建与推送、目标集群的授权发布，并确认重启后数据仍在。镜像架构与跨架构模拟验收记录会随交付提供；x86 上的 QEMU 验收不能替代目标 ARM64 内核、Docker、防火墙和现场云环境验收。

## 9. 从旧部署迁移

先停止旧服务并备份其完整数据目录和配置。不要让新旧服务同时写同一个 SQLite 数据库。

在一个空部署目录完成首次安装并停止新容器，再将旧数据目录的**全部内容**恢复到新部署的 `data/`（包括数据库、主密钥、任务与 Docker 配置），把旧配置与敏感环境值合并到 `config/`，核对所有容器内路径后启动。覆盖新安装时随机生成的测试数据前，应确认旧备份可用并保留副本。不得只拷 `builder.db` 而遗漏密钥。

原服务如果也占用 8082，应先用其他临时端口验收迁移结果，完成切换时只保留一个服务提供正式入口。此次制包不会自动迁移或替换当前运行中的服务。
