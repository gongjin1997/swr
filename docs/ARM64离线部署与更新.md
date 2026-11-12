# ARM64 离线部署与补丁更新

适用于平台 v2（数据存储在 PostgreSQL / GaussDB）在 Linux aarch64 / arm64 主机上的内网离线部署。
默认部署目录 `/data/swr`，访问端口 **8082**（容器内 8080）。

交付物分两种，每个文件都附同名 `.sha256`：

| 交付物 | 文件 | 内容 | 用途 |
| --- | --- | --- | --- |
| 全量离线包 | `swr-builder-arm64-<版本>.tar.gz` | 平台运行镜像、PostgreSQL 镜像、代码、安装与管理脚本、铺底资源、离线依赖、源码 | 首次部署；运行依赖、PostgreSQL 版本或管理工具变化时升级 |
| 补丁包 | `swr-patch-<版本>.tar.gz` | 不含镜像。可含完整代码（后端与编译后的前端）、DDL、DML 脚本 | 日常的代码修复与数据库变更 |

目录：

1. [部署架构](#1-部署架构)
2. [目标主机准备](#2-目标主机准备)
3. [首次全量安装](#3-首次全量安装)
4. [目录结构与配置](#4-目录结构与配置)
5. [铺底资源](#5-铺底资源)
6. [补丁包](#6-补丁包)
7. [完整包升级](#7-完整包升级)
8. [备份、回滚与恢复](#8-备份回滚与恢复)
9. [从 v1（SQLite）迁移](#9-从-v1sqlite迁移)
10. [日常运维](#10-日常运维)
11. [故障排查](#11-故障排查)
12. [验收清单](#12-验收清单)
13. [安全要求](#13-安全要求)
14. [制作交付物（联网构建机）](#14-制作交付物联网构建机)

## 1. 部署架构

```text
ARM64 主机（Docker）
├─ swr-builder    平台：运行镜像 + 只读挂载的代码版本；宿主机 8082 → 容器 8080；通过宿主机 Docker 构建业务镜像
├─ swr-postgres   内置 PostgreSQL 16：数据在 /data/swr/pgdata，不映射宿主机端口
│                 两个容器在 Docker 网络 swr 中，平台按容器名 swr-postgres 连接数据库
└─ 一次性容器     swrctl 管理命令、数据库迁移、补丁 SQL；执行完即删除
```

- 运行镜像固定了 Python、Node、Docker CLI 和全部依赖。代码按版本放在 `releases/<版本>/`，启动时只读挂载进容器，所以补丁只需要换代码目录，不需要镜像。
- `install.sh`、`upgrade.sh`、`swrctl` 都在运行镜像中执行，关闭网络，通过宿主机 Docker socket 操作容器。目标主机不需要安装 Python、Node，也不需要联网。
- 平台自己的表结构变化写在程序的迁移里，随代码发布，新代码启动前执行。补丁中的 DDL / DML 在迁移之后执行，每个脚本只执行一次，执行记录保存在数据库的 `patch_scripts` 表。
- 也可以不用内置数据库，改用现场已有的 PostgreSQL 14+，或 PG 兼容模式的 GaussDB（[3.3](#33-使用外部-postgresql--gaussdb)）。

## 2. 目标主机准备

| 项目 | 要求 |
| --- | --- |
| 架构 | aarch64 / arm64；不需要 QEMU |
| 系统 | 支持 Docker 的 Linux（cgroups、namespace、overlayfs），使用 systemd |
| Docker | 已运行的 Docker Engine，建议使用包内验证过的 28.5.2（`tools/` 中有离线安装包） |
| 账号 | root，或能 sudo 的运维账号；以下命令都以 sudo 执行 |
| 磁盘 | `/data/swr` 至少 10 GB（数据库、备份、构建上下文与日志按业务规模另加）；Docker 数据目录（默认 `/var/lib/docker`）至少 5 GB 用于导入镜像 |
| 端口 | 8082 未被占用，防火墙放行预期的客户端 |
| 网络 | 能访问现场 SWR 与 Kubernetes API Server；不需要访问互联网 |

同一个部署目录只能运行一套平台；不要让两套平台同时写同一个数据库。

检查：

```sh
uname -m                                   # aarch64
docker version
docker info --format '{{.Architecture}}'   # aarch64 或 arm64
df -h /data /var/lib/docker
ss -lnt | grep ':8082 '                    # 没有输出表示端口空闲
```

### 2.1 主机尚未安装 Docker

先校验并解开完整包（[3.1](#31-校验并解开完整包)），再执行：

```sh
sudo sh tools/install-docker.sh
```

脚本要求 root、systemd 和 iptables。检测到已有 Docker 或 `docker.service` 时停止，不会替换。缺少 iptables 时，从目标发行版的离线 RPM / DEB 介质安装后再运行；不要通过关闭 Docker 防火墙绕过。已有 Docker 的主机跳过本步骤。

## 3. 首次全量安装

### 3.1 校验并解开完整包

把完整包和 `.sha256` 拷到目标主机同一目录。下例版本为 `2.0.0`：

```sh
sha256sum -c swr-builder-arm64-2.0.0.tar.gz.sha256
sudo mkdir -p /data/swr/packages
sudo tar -xzf swr-builder-arm64-2.0.0.tar.gz -C /data/swr/packages
cd /data/swr/packages/swr-builder-arm64-2.0.0
```

| 路径 | 内容 |
| --- | --- |
| `images/swr-builder-arm64.tar.gz` | 平台运行镜像（`docker save`），含 Python 依赖、Docker CLI、前端构建工具 |
| `images/postgres-arm64.tar.gz` | 内置数据库 PostgreSQL 16 镜像（按摘要固定） |
| `releases/swr-code-<版本>.tar.gz` | 代码与编译后的前端，含逐文件 SHA256 与运行依赖标识 |
| `seed/` | 铺底资源：`sql/`、`images/`、`certs/`（[第 5 节](#5-铺底资源)），没有铺底资源时只有说明文件 |
| `install.sh` / `upgrade.sh` / `swrctl` / `ops/` | 首次安装、完整包升级、日常管理 |
| `config.yaml` / `swr-builder.env.example` | 配置模板，不含现场信息 |
| `tools/` | Docker 28.5.2 静态离线安装包与安装脚本 |
| `dependencies/` | Python 依赖清单、带哈希的锁与全部 ARM64 wheel |
| `source.tar.gz` | 本版本的完整源码，供离线修改后制作补丁 |
| `runtime-image` / `postgres-image` | 两个镜像的标签 |
| `manifest.json` / `SHA256SUMS` | 镜像 ID、PostgreSQL 主版本、运行依赖标识、铺底资源清单；包内文件校验 |

### 3.2 安装（内置 PostgreSQL）

```sh
sudo sh install.sh /data/swr 8082
sudo /data/swr/swrctl status
sudo /data/swr/swrctl verify
```

`install.sh` 依次：

1. 校验包内全部文件的 SHA256；
2. 导入运行镜像与 PostgreSQL 镜像，确认都是 arm64；
3. 创建目录与配置；随机生成数据库口令，写入 `config/postgres.env` 与 `config/swr-builder.env`（600 权限）；
4. 导入铺底资源中的业务基础镜像与证书；
5. 创建 Docker 网络 `swr`，启动 `swr-postgres` 并等待就绪；
6. 执行数据库迁移；指定了 v1 数据时导入（[第 9 节](#9-从-v1sqlite迁移)）；执行铺底 SQL；
7. 启动平台，等待健康检查通过。

安装不会覆盖已有的部署、配置、数据或同名容器，也不执行 `pip install`、`npm install` 或联网拉取镜像。

首次管理员为 `admin`，初始随机密码只在目标主机上读取：

```sh
sudo cat /data/swr/data/admin-initial-password.txt
```

### 3.3 使用外部 PostgreSQL / GaussDB

数据库要求：

- PostgreSQL 14 及以上；或 GaussDB / openGauss 中以 `DBCOMPATIBILITY 'PG'` 创建的数据库。
- 离线镜像只带 psycopg 驱动：GaussDB 需要允许 MD5 认证（`password_encryption_type` 为 0 或 1）。只允许 SHA256 认证时需要华为 gaussdb 驱动，离线镜像中没有，需要另做包含该驱动的完整包。
- 新建一个空库和专用账号，账号要能在库中建表。
- 地址必须能从容器内访问。不要写 `127.0.0.1`，它指向容器自己；数据库在本机时写本机的内网 IP。

先准备一个环境文件（只放在目标主机上，600 权限）：

```sh
sudo install -m 600 /dev/null /root/swr-site.env
sudo vi /root/swr-site.env
```

```text
BUILDER_DATABASE_URL=postgresql://swr@10.0.0.20:5432/swr
BUILDER_DATABASE_PASSWORD=<数据库口令>
```

然后安装：

```sh
sudo env SWR_DATABASE=external SWR_ENV_FILE=/root/swr-site.env sh install.sh /data/swr 8082
```

安装时检查连接，库中已有平台账号时拒绝（全新安装需要空库）。

使用外部数据库时，平台的备份只包含配置与数据文件，**数据库的备份由 DBA 负责**；补丁或升级会修改数据库时，必须先由 DBA 备份，再确认执行（[6.6](#66-使用外部数据库时)）。

### 3.4 安装选项

在 `install.sh` 前用 `sudo env 变量=值 ... sh install.sh <目录> <端口>` 指定：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `SWR_DATABASE` | `bundled` | `external`：不启动内置数据库，使用外部数据库（必须同时给 `SWR_ENV_FILE`） |
| `SWR_ENV_FILE` | 无 | 预先填写的环境变量文件（仓库凭证、外部数据库连接等），复制为 `config/swr-builder.env`；使用内置数据库时不要在其中写 `BUILDER_DATABASE_*` |
| `SWR_IMPORT_V1` | 无 | v1 部署目录，导入其数据（[第 9 节](#9-从-v1sqlite迁移)） |
| `SWR_CONTAINER_NAME` | `swr-builder` | 平台容器名 |
| `SWR_DB_CONTAINER_NAME` | `swr-postgres` | 内置数据库容器名 |
| `SWR_BIND_ADDRESS` | `0.0.0.0` | 端口只监听指定地址，例如只给本机反向代理用时设为 `127.0.0.1` |
| `SWR_DOCKER_NETWORK` | 内置数据库为 `swr`，外部数据库为 `bridge` | Docker 网络。内置数据库必须是自定义网络（不存在时自动创建）；外部数据库可用 `host` |

`SWR_ALLOW_EMULATION=1` 只用于 x86 构建机上的隔离验收，不要在目标机上用它绕过架构检查。

### 3.5 首次登录后

1. 访问 `http://<主机IP>:8082`，用 `admin` 和初始密码登录，按提示修改密码；
2. 「系统设置 → 镜像仓库」填写现场 SWR 地址、组织和账号（密码也可以写在 `config/swr-builder.env`，见 [4.2](#42-配置文件与环境变量)）；
3. 「集群与命名空间」粘贴 kubeconfig 纳管集群（支持 `kind: Config` 中的静态 Token 或内嵌客户端证书，不执行 `exec` / `auth-provider`）；
4. 建项目、应用，做一次真实的构建与发布（[第 12 节](#12-验收清单)）。

通过 HTTPS 反向代理访问后，把 `config/config.yaml` 中的 `server.secure_cookie` 改为 `true` 并执行 `swrctl restart`。

### 3.6 安装失败

安装中途失败时，`install.sh` 会删除已创建的容器，把本次生成的文件（含数据库数据目录）移到 `/data/swr/failed-install-<时间>/`，部署目录恢复为可以重新安装的状态。根据前面的错误信息处理后，重新执行 `install.sh`；确认不需要后删除 `failed-install-*` 目录。

常见原因：端口被占用、同名容器已存在、外部数据库连不上、磁盘空间不足。已导入的镜像会保留，不影响重试。

## 4. 目录结构与配置

### 4.1 目录

```text
/data/swr/
  swrctl                      管理入口
  deploy.json                 部署记录：代码版本、运行镜像、端口、数据库、已应用的补丁
  history.log                 操作记录（安装、补丁、升级、回滚、恢复），每行一条 JSON
  runtime-image               swrctl 使用的运行镜像 ID
  current -> releases/<版本>  当前代码
  releases/<版本>/            不可变的代码与前端产物（逐文件 SHA256）
  patches/<版本>/             已应用补丁的 patch.json、README.md 与 sql/
  config/config.yaml          平台配置，容器内 /etc/swr/config.yaml（只读）
  config/swr-builder.env      环境变量：数据库连接、仓库凭证等（600）
  config/postgres.env         内置数据库的初始化参数与口令（600）
  config/certs/               CA / 客户端证书，容器内 /etc/swr/certs/
  data/                       平台数据：secret.key、构建与发布日志、上传文件，容器内 /var/lib/swr
  pgdata/                     内置 PostgreSQL 的数据目录（使用外部数据库时没有）
  backups/backup-<时间>/      停机一致备份（第 8 节）
  updates/                    待应用的补丁包，以及 build-patch 的输出
  source/                     首次交付的源码；source-versions/<版本>/ 是完整包升级带来的新版源码
  ops/                        管理工具
  tmp/                        解包与预检的临时目录
  packages/                   建议用来存放完整包
```

不要手工修改 `releases/` 与 `current`（会破坏完整性校验），不要删除 `pgdata/`、`data/secret.key`。

### 4.2 配置文件与环境变量

`config/config.yaml` 是平台配置，修改后执行 `sudo /data/swr/swrctl restart`。仓库、Kubernetes、构建、安全等设置大多也能在「系统设置」页面保存，无需重启。优先级：**环境变量 > 页面设置 > config.yaml > 默认值**。

`config/swr-builder.env` 每行一个 `NAME=value`，不要写 `export`，也不要加引号。常用变量：

| 变量 | 用途 |
| --- | --- |
| `SWR_USERNAME` / `SWR_PASSWORD` | SWR 登录账号与长期有效登录密钥；也可改用 `SWR_AK` / `SWR_SK` / `SWR_PROJECT` |
| `HCS_AK` / `HCS_SK` 等 | HCS 后端接口凭证（默认不启用 HCS） |
| `BUILDER_ADMIN_PASSWORD` | 只在首次启动创建 admin 时使用 |
| `BUILDER_SECRET_KEY` | 可选：由运维托管的加密主密钥；设置后必须安全保存，不能更换 |

文件末尾“内置 PostgreSQL 连接：由 swrctl 生成”的两行（`BUILDER_DATABASE_URL`、`BUILDER_DATABASE_PASSWORD`）由安装工具生成，口令同时用于初始化数据库，不要修改。

端口（安装时指定）、数据目录（容器内 `/var/lib/swr`）、数据库连接由部署工具管理，不要通过 config.yaml 或页面修改。

### 4.3 证书与仓库信任

自签名 CA 放在 `/data/swr/config/certs/`（也可以放进铺底资源，安装时自动复制），配置中的路径写 `/etc/swr/certs/<文件名>`。

Docker daemon 访问仓库时，还需要在宿主机安装 CA：`/etc/docker/certs.d/<仓库地址>/ca.crt`。平台的 Registry API TLS 设置不影响 Docker daemon。仓库证书的 SAN 与访问地址不匹配时，安装 CA 无法解决名称校验，需要修复仓库证书，或者只在测试环境把地址加入 Docker daemon 的 `insecure-registries` 并重启 Docker：

```json
{
  "insecure-registries": ["<仓库主机名>"]
}
```

编辑 `/etc/docker/daemon.json` 时保留并合并现有配置。重启 Docker 会影响该主机上的所有容器，平台不会自动执行。`insecure-registries` 会降低该仓库的 TLS 保护，Docker 官方建议优先使用名称匹配、由受信任 CA 签发的证书（[dockerd 配置](https://docs.docker.com/reference/cli/dockerd/)、[仓库证书](https://docs.docker.com/engine/security/certificates/)）。

### 4.4 内置数据库

- PostgreSQL 16.15，容器 `swr-postgres`，库 `swr`，账号 `swr`；只在 Docker 网络 `swr` 内可访问，不映射宿主机端口。
- 需要手工查询时：`sudo docker exec -it swr-postgres psql -U swr swr`。
- 两个容器的重启策略都是 `unless-stopped`：主机重启后自动启动；数据库就绪前平台可能自动重启几次。
- 数据库主版本（16）升级不能直接替换数据目录，目前的工具会拒绝；小版本随完整包升级（[第 7 节](#7-完整包升级)）。

## 5. 铺底资源

完整包的 `seed/` 放首次部署需要的现场资源，制作方法见 [14.1](#141-全量离线包)：

| 目录 | 内容 | 安装时 |
| --- | --- | --- |
| `seed/sql/` | 铺底 SQL：`ddl/`、`dml/`、`*.sql` | 迁移（和 v1 数据导入）之后、平台首次启动之前执行一次，记在补丁名 `seed` 下 |
| `seed/images/` | 业务构建用的基础镜像（`docker save` 生成，linux/arm64） | `docker load` 到目标主机 |
| `seed/certs/` | CA 等证书 | 复制到 `config/certs/`；已有同名文件时保留现有文件 |

铺底 SQL 的写法与补丁 SQL 相同（[6.2](#62-ddl--dml-编写规则)），另外：

- 以后的完整包可以追加新的铺底脚本（例如 `dml/002_more.sql`），升级时只执行新增的；**已经发布的铺底脚本不能修改**，内容变化会在停机前被拒绝。
- 不要插入账号：admin 由平台首次启动时创建；从 v1 迁移时铺底 SQL 在导入之后执行，不要与导入的数据冲突。

业务基础镜像导入后，应用的“基础镜像”填写与导入镜像相同的完整名称即可直接使用；也可以导入后 `docker tag`、`docker push` 到现场 SWR。

## 6. 补丁包

### 6.1 补丁包内容

```text
swr-patch-2.0.1.tar.gz
  patch.json        版本、适用版本（base）、说明、运行依赖标识、代码与 SQL 清单、逐文件 SHA256
  README.md         可选：给现场运维的说明
  code/             可选：完整代码（release.json + swr_builder/，含编译后的前端），版本号与补丁相同
  sql/ddl/*.sql     可选：结构变更（建表、加列、索引……）
  sql/dml/*.sql     可选：数据变更（数据修正、铺底数据……）
  sql/*.sql         可选：在 ddl/、dml/ 之后执行
```

- 代码与 SQL 至少有一项。只有 SQL 的补丁不改变代码版本。
- 补丁不含镜像。新增 Python 库、修改前端依赖、升级 Python / Node / 基础镜像需要完整包（[第 7 节](#7-完整包升级)）。补丁中的代码记录了运行依赖标识，与现场不一致时在停机前拒绝。
- 版本号只能包含字母、数字、`.`、`_`、`-`，最长 80 个字符，每个补丁唯一、不能重复使用。建议代码补丁用 `2.0.1`、`2.0.2`，只有 SQL 时用 `2.0.1-sql1`。
- `base` 指定补丁只能应用到某个版本（现场 `swrctl status` 显示的 `version`）。不指定时可以应用到任何版本，这时 SQL 要能在各个版本上执行。

### 6.2 DDL / DML 编写规则

示例：

```sql
-- sql/ddl/001_site_cost_centers.sql
CREATE TABLE site_cost_centers (
    id BIGSERIAL PRIMARY KEY,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    CONSTRAINT uq_site_cost_centers_code UNIQUE (code)
);
```

```sql
-- sql/dml/001_cost_centers.sql：可重复执行的写法
INSERT INTO site_cost_centers(code, name)
SELECT 'FIN', '财务' WHERE NOT EXISTS (SELECT 1 FROM site_cost_centers WHERE code = 'FIN');
```

```sql
-- swr:no-transaction
CREATE INDEX CONCURRENTLY idx_site_cost_centers_name ON site_cost_centers (name);
```

规则：

1. **文件**：文件名只用字母、数字、`.`、`_`、`-`，以 `.sql` 结尾，UTF-8 编码。按 `ddl/` → `dml/` → 根目录的顺序执行，同一目录内按文件名排序，建议用 `001_`、`002_` 序号开头。`README.md` 等说明文件会被忽略，其他文件或子目录会被拒绝。
2. **事务**：每个脚本在一个事务中执行，并和它的执行记录一起提交。任一语句失败，整个脚本回滚，后面的脚本不再执行，补丁整体失败并自动恢复（[6.5](#65-失败时的自动恢复)）。
3. **不要自己写** `BEGIN;` / `COMMIT;`。确实需要在事务外执行的语句（例如 `CREATE INDEX CONCURRENTLY`），把 `-- swr:no-transaction` 写在第一行，并且**一个脚本只放一条语句**：PostgreSQL 会把一次发送的多条语句当作一个事务执行，这类语句会报错。事务外的脚本失败时可能已经部分生效。
4. **只执行一次**：同一补丁中同一路径的脚本只执行一次（按补丁版本 + 路径记录 SHA256）。**已经执行过的脚本不能修改**，修改后会在停机前被拒绝；需要修正时新增一个脚本，或发新的补丁。
5. **不支持** psql 元命令（`\c`、`\i`、`\set` 等以反斜杠开头的行）和 `COPY ... FROM STDIN`；大批量数据用 `INSERT ... VALUES` 分批写。
6. **平台表结构**由开发写成程序迁移、随代码发布，补丁 DDL 不要修改平台自己的表（否则以后的迁移可能冲突）。补丁 DDL 用于现场自建的表和索引；DML 用于数据修正和铺底数据。
7. **可重复执行**：DML 尽量写成重复执行结果不变的形式，例如 `INSERT ... SELECT ... WHERE NOT EXISTS (...)`、带条件的 `UPDATE`。
8. **兼容**：使用外部 GaussDB 时，只用 PostgreSQL 与 GaussDB 共有的语法，不要用 `ON CONFLICT`、`MERGE`、`SKIP LOCKED`；先在测试库上执行验证。
9. **耗时与锁**：`ALTER TABLE` 会锁表，大表的数据修正耗时较长，都放在维护窗口；补丁执行期间平台已停止。
10. 不要在脚本中写口令、密钥等敏感数据。

### 6.3 制作补丁

#### 在开发 / 构建机制作（推荐）

需要 Node 22、Python 3.11，以及与现场完整包依赖一致的源码（`deploy/offline/runtime-lock.json` 相同）。不需要 ARM64 机器：前端产物是静态文件，后端是纯 Python。

```sh
# 先把 swr_builder/__init__.py 的 __version__ 改成补丁版本（页面上显示的是它）
python3 scripts/build-arm64-patch.py 2.0.1 --base 2.0.0 \
  --sql patches/2.0.1/sql --readme patches/2.0.1/README.md --description "修复……" \
  --bundle-manifest /path/to/swr-builder-arm64-2.0.0/manifest.json
# 只有 SQL：
python3 scripts/build-arm64-patch.py 2.0.0-sql1 --no-code --sql patches/2.0.0-sql1/sql
```

输出在 `artifacts/patches/swr-patch-<版本>.tar.gz` 与 `.sha256`。脚本会：检查运行依赖没有变化（`--bundle-manifest` 再与现场完整包核对）；执行 `npm ci`、`npm run build`；用与现场相同的规则校验 SQL；生成逐文件 SHA256。

#### 在目标主机上离线制作

修改 `/data/swr/source/` 中的代码（完整包升级后用 `source-versions/<版本>/`），SQL 放在部署目录内，例如 `/data/swr/patch-src/2.0.1/sql/`：

```sh
sudo /data/swr/swrctl build-patch 2.0.1 --sql /data/swr/patch-src/2.0.1/sql --base 2.0.0 --description "修复……"
# 只有 SQL：
sudo /data/swr/swrctl build-patch 2.0.0-sql1 --no-code --sql /data/swr/patch-src/2.0.0-sql1/sql
```

输出在 `/data/swr/updates/`。这个命令使用运行镜像中预装的 Node 与前端依赖，关闭网络执行；源码、SQL 目录和 `--readme` 都必须在部署目录内。前端或 Python 依赖锁与运行镜像不一致时拒绝，需要新的完整包。

### 6.4 应用补丁

在维护窗口执行，并暂停提交新的构建和发布：

```sh
sudo cp swr-patch-2.0.1.tar.gz swr-patch-2.0.1.tar.gz.sha256 /data/swr/updates/
cd /data/swr/updates && sha256sum -c swr-patch-2.0.1.tar.gz.sha256
sudo /data/swr/swrctl apply /data/swr/updates/swr-patch-2.0.1.tar.gz
sudo /data/swr/swrctl status
sudo /data/swr/swrctl verify
```

补丁包必须放在部署目录内（管理容器只挂载部署目录）。输出示例（省略了日志行）：

```text
[1/5] 预检（应用仍在运行）
  将执行数据库迁移：[5]
  SQL（2.0.1）：2 个将执行，0 个已执行过将跳过
[2/5] 停止应用
[3/5] 备份
备份已保存：/data/swr/backups/backup-20261120-213000-123456
[4/5] 切换代码并更新数据库
已应用迁移：[1, 2, 3, 4, 5]
执行 ddl/001_site_cost_centers.sql
执行 dml/001_cost_centers.sql
2.0.1：执行 2 个脚本，跳过 0 个
[5/5] 启动并等待健康检查
已切换到 2.0.1，健康检查通过；更新前的备份：/data/swr/backups/backup-20261120-213000-123456
```

预检在停机之前完成，任何一项不通过都不会停服务：

- 包与 `.sha256` 一致，逐文件 SHA256 与 `patch.json` 一致，目录结构符合约定；
- 补丁没有应用过；`base` 与当前版本一致；代码需要的运行镜像已安装；`releases/` 中没有内容不同的同版本代码；
- 没有未结束的构建与发布；
- 新代码认识数据库中的全部迁移；
- 用新代码校验 SQL，并与数据库中的执行记录比对（已执行过的脚本被修改时拒绝）。

停机时间约为：备份（数据库导出与打包 `data/`）+ 迁移与 SQL + 平台启动（通常在 30 秒内）。

### 6.5 失败时的自动恢复

备份之后的任何一步失败（迁移、SQL、启动、健康检查，以及按 Ctrl+C 或收到终止信号），控制器都会：

1. 删除新版本的平台容器；
2. 内置数据库：删除并重建数据库，用本次备份的导出恢复（`pg_restore`），已执行的迁移与 SQL 全部撤销；
3. 恢复 `config/` 与 `data/`；
4. 切回原版本代码并启动。

输出“已恢复更新前的代码、配置、数据文件和数据库，应用已用原版本启动”，`swrctl history` 中记录失败原因。恢复本身也失败时，平台保持停止，错误信息会给出 `swrctl restore <备份目录>` 命令。

失败补丁中的代码会留在 `releases/<版本>/`（内容已校验，不影响运行）；修正后用**新的版本号**重新出包。如果备份这一步就失败（例如磁盘空间不足），控制器直接用原版本重新启动，没有任何改动。

### 6.6 使用外部数据库时

- 控制器无法备份外部数据库，备份只包含配置与数据文件。
- 预检判断本次是否会修改数据库（有待执行的迁移或 SQL）。会修改时，必须由 DBA 先备份数据库，再加 `--db-backup-confirmed`：

  ```sh
  sudo /data/swr/swrctl apply /data/swr/updates/swr-patch-2.0.1.tar.gz --db-backup-confirmed
  ```

- 只有代码、没有新迁移的补丁不需要确认。
- 失败时只恢复代码和文件，**数据库中已执行的迁移或 SQL 不会撤销**，由 DBA 用变更前的备份恢复。原版本代码不认识新迁移时无法启动，平台保持停止，DBA 恢复数据库后执行 `swrctl start`。

### 6.7 查看补丁记录

- `swrctl status` 的 `patches`：已应用的补丁版本；
- `/data/swr/patches/<版本>/`：当时的 `patch.json`、`README.md` 与 `sql/`；
- `swrctl history`：每次操作的时间、结果、所用备份；
- 数据库：`SELECT patch, path, applied_at FROM patch_scripts ORDER BY id;`。

### 6.8 v1 的代码包

`swrctl apply`（以及兼容的 `swrctl update`）也接受 v1 方式的 `swr-code-<版本>.tar.gz`，按只有代码的补丁处理（同样备份数据库并执行迁移）。`swrctl build-update <源码> <版本>` 等同于 `swrctl build-patch <版本> --source <源码>`。

## 7. 完整包升级

以下情况需要新的完整包：新增或升级 Python 库、修改前端依赖（`web/package-lock.json`）、升级 Python / Node / Docker CLI 基础镜像、升级 PostgreSQL 小版本、管理工具（`ops/`、`swrctl`）有改动、需要新的铺底资源。

```sh
sha256sum -c swr-builder-arm64-2.1.0.tar.gz.sha256
sudo tar -xzf swr-builder-arm64-2.1.0.tar.gz -C /data/swr/packages
cd /data/swr/packages/swr-builder-arm64-2.1.0
sudo sh upgrade.sh /data/swr
sudo /data/swr/swrctl verify
```

`upgrade.sh`：

1. 校验完整包，导入新的运行镜像（和 PostgreSQL 镜像）；
2. 登记新运行镜像，旧镜像保留，用于回滚；
3. 按补丁相同的流程切换到新版本：预检 → 停机 → 备份 → 切换代码 → 迁移 → 执行新增的铺底 SQL → 启动与健康检查，失败时自动恢复；
4. PostgreSQL 小版本变化时，在备份之后用新镜像重建数据库容器（数据目录不变）；主版本变化时拒绝升级；
5. 更新 `ops/`、`swrctl`、部署文档，导入新的铺底镜像与证书；新版源码放在 `source-versions/<版本>/`，不覆盖 `source/` 中尚未发布的修改。

使用外部数据库时，DBA 备份后执行 `sudo env SWR_DB_BACKUP_CONFIRMED=1 sh upgrade.sh /data/swr`。

回滚窗口内不要执行 `docker image prune -a` 或 `docker system prune -a`：它们会删除回滚需要的旧运行镜像。

## 8. 备份、回滚与恢复

### 8.1 备份

```sh
sudo /data/swr/swrctl backup     # 停止平台 → 导出数据库并打包 config/、data/ → 恢复运行
sudo /data/swr/swrctl backups    # 列出备份：时间、说明、当时的版本与补丁
```

每次 `apply`、`upgrade`、`rollback`、`restore` 之前也会自动备份。备份目录：

| 文件 | 内容 |
| --- | --- |
| `database.dump` | 内置数据库的 `pg_dump` 导出（使用外部数据库时没有） |
| `files.tar.gz` | `config/` 与 `data/`（含 `secret.key`、环境文件） |
| `snapshot.json` | 备份时的部署记录：代码版本、运行镜像、补丁 |
| `SHA256SUMS` | 以上文件的校验值 |

- 数据库与 `secret.key` 必须一起保存：缺少主密钥时，已保存的仓库、集群凭证无法解密。
- 备份含敏感信息，应按生产数据保护，并复制到其他存储，例如：
  `sudo tar -C /data/swr/backups -czf /mnt/backup/swr-backup-20261120-213000-123456.tar.gz backup-20261120-213000-123456`
- 备份不会自动清理。定期删除旧备份，至少保留最近一次和最近一次升级前的备份。

### 8.2 回滚：只切换代码

```sh
sudo /data/swr/swrctl rollback          # 回到上一个版本
sudo /data/swr/swrctl rollback 2.0.0    # 切到 releases/ 中保留的任一版本（也可以向前切）
```

- 只换代码（和对应的运行镜像），保留当前数据；补丁执行过的 SQL **不会撤销**。
- 目标版本不认识数据库中的迁移时拒绝：只回滚代码会无法启动，这时用恢复（8.3）回到升级前的备份。
- 回滚到旧运行镜像的版本时自动使用旧镜像，要求它仍在 Docker 中。

### 8.3 恢复

```sh
sudo /data/swr/swrctl backups
sudo /data/swr/swrctl restore /data/swr/backups/backup-20261120-213000-123456
```

- 数据库、`config/`、`data/`、代码版本和补丁记录一起回到备份时间，**备份之后的业务数据会被替换**；进行中的任务也会丢失。
- 恢复前自动备份当前状态；当前数据库已损坏、无法导出时加 `--skip-safety-backup`。
- 使用外部数据库时只恢复文件，需要 DBA 把数据库恢复到同一时间点。
- 恢复需要备份时的代码版本仍在 `releases/` 中、对应的运行镜像仍在 Docker 中。

## 9. 从 v1（SQLite）迁移

v1（例如 `20261109-r1`）把数据保存在 `/data/swr/data/builder.db`。v2 不能在 v1 的目录中运行，需要安装到**新目录**并导入 v1 的数据。v1 目录不会被修改，可以随时切回。

1. 在 v1 上确认没有进行中的构建与发布，记录版本（`sudo /data/swr/swrctl status`）。
2. 停止 v1 并备份：

   ```sh
   sudo /data/swr/swrctl stop
   sudo /data/swr/swrctl backup
   ```

3. 解开 v2 完整包（不要放在 v1 目录中），安装到新目录并导入：

   ```sh
   sudo mkdir -p /data/packages
   sudo tar -xzf swr-builder-arm64-2.0.0.tar.gz -C /data/packages
   cd /data/packages/swr-builder-arm64-2.0.0
   sudo env SWR_IMPORT_V1=/data/swr sh install.sh /data/swr2 8082
   ```

   安装工具会：确认 v1 已停止；把 v1 `data/` 中除 `builder.db` 外的内容（`secret.key`、日志、上传文件等）复制到新目录；沿用 v1 的 `config.yaml`、环境变量（数据库相关的除外）和证书；在平台首次启动前把 `builder.db` 的副本导入 PostgreSQL；再执行铺底 SQL 并启动。账号、密码、设置、应用和历史记录保持不变。v1 已停止的同名容器会被删除（v1 的 `swrctl start` 会按记录重新创建）。

4. 验证：用原账号登录，核对应用、构建记录、集群设置，做一次测试构建，执行 `sudo /data/swr2/swrctl verify`。
5. 需要切回 v1 时（两套平台的容器默认同名，v1 的工具不会操作其他目录的容器，所以先删除 v2 已停止的平台容器；
   它不保存数据，v2 的 `swrctl start` 会重新创建）：

   ```sh
   sudo /data/swr2/swrctl stop --with-db
   sudo docker rm swr-builder
   sudo /data/swr/swrctl start
   ```

   再切到 v2：`sudo /data/swr/swrctl stop`、`sudo docker rm swr-builder`、`sudo /data/swr2/swrctl start`。
   迁移后在 v2 中产生的数据不会出现在 v1 中。

6. 确认无误后保留 v1 目录一段时间再归档。不要同时运行两套平台。

v1 不是用离线工具部署的（例如 systemd）时，`SWR_IMPORT_V1` 指向包含 `builder.db` 与 `secret.key` 的数据目录，配置需要在新目录中手工填写。

## 10. 日常运维

| 命令（`sudo /data/swr/swrctl …`） | 作用 |
| --- | --- |
| `status` | 代码版本、上一版本、已应用补丁、运行镜像、端口、平台与数据库容器状态 |
| `verify` | 代码完整性、ARM64 依赖、数据库迁移与代码一致、平台与数据库健康 |
| `logs [--tail 200]` / `logs --db` | 平台日志 / 数据库日志 |
| `start` | 启动（数据库没有运行时一起启动） |
| `stop [--with-db]` | 停止平台（先检查未结束的任务）；`--with-db` 同时停止数据库 |
| `restart` | 重启平台（先检查任务），修改配置后使用 |
| `backup` / `backups` / `restore <目录>` | 备份、列出备份、恢复（第 8 节） |
| `apply <补丁包>` / `rollback [版本]` | 应用补丁、切换代码版本（第 6、8 节） |
| `build-patch <版本> …` | 在目标机制作补丁（6.3） |
| `history` | 操作记录 |

```sh
curl -fsS http://127.0.0.1:8082/healthz
```

- 同一时间只能执行一个管理命令。补丁包、SQL 目录等路径必须在部署目录内；在部署目录中执行时可以写相对路径。
- 长时间操作（补丁、升级、恢复）建议在 tmux / screen 中执行。SSH 断开不会中断操作，重新登录后用 `swrctl status`、`swrctl history` 查看结果。
- 容器日志按 20 MB × 5 个文件轮转。
- 健康接口正常不代表已验证 SWR 推送或 Kubernetes 发布，现场需要做一次真实的构建与发布。

## 11. 故障排查

| 现象 | 原因与处理 |
| --- | --- |
| “目标 Docker 是 x86_64” | 不是 ARM64 主机，换用 ARM64 主机 |
| “完整包校验失败” | 传输损坏：重新拷贝，先用 `sha256sum -c` 校验 |
| “容器 … 已存在，拒绝重复安装” | 主机上有同名容器：确认无用后 `docker rm -f` 删除，或用 `SWR_CONTAINER_NAME` / `SWR_DB_CONTAINER_NAME` 换名 |
| “同名容器 … 不属于此部署目录” | 另一个部署目录（例如 v1）的容器占用了这个名字：先停止它，`docker rm` 删除后再执行（第 9 节第 5 步） |
| “… 已有内容，拒绝覆盖” | 目录不是空的：换新目录，或确认旧数据无用后移走 |
| 安装失败并出现 `failed-install-*` | 按前面的错误处理后重新 `install.sh`（3.6） |
| “应用健康检查失败” / “容器未保持运行” | `swrctl logs` 查看：常见是 config.yaml 格式错误、数据库连不上、端口冲突 |
| “数据库健康检查失败” | `swrctl logs --db`：常见是磁盘已满 |
| “有 N 个未结束的构建 / 发布” | 等待完成或在页面取消；维护期间暂停提交新任务 |
| “此代码需要的运行镜像未安装” | 补丁的运行依赖与现场不同：需要对应的完整包 `upgrade.sh` |
| “补丁 X 只能用于版本 Y” | `base` 不匹配：先应用前置补丁，或请开发重新出包 |
| “… 已执行过，但内容与本次不同” | 已发布的脚本被修改：请开发改为新增脚本 |
| “releases/X 已存在且内容不同” | 版本号重复（例如失败后修正的补丁）：换新的版本号 |
| “不认识数据库中的迁移” | 回滚跨过了迁移：用 `swrctl restore` 回到当时的备份 |
| “自动恢复也失败” | 平台保持停止：处理错误原因后执行提示中的 `swrctl restore` |
| “使用外部数据库……--db-backup-confirmed” | DBA 备份数据库后加该参数重试 |
| “已有安装、更新或管理命令正在执行” | 其他会话正在执行 swrctl，等待结束 |
| “此目录是 v1 的部署” | 对 v1 目录执行了 v2 的命令：按第 9 节迁移 |
| 构建时 docker 登录 / 推送证书错误 | 宿主机 Docker 未信任仓库 CA（4.3） |

## 12. 验收清单

首次安装：

- [ ] `swrctl status`：`container_state` 为 `running`，`health` 与 `database.health` 为 `healthy`
- [ ] `swrctl verify` 通过
- [ ] 登录并修改初始密码；（迁移时）原账号可登录
- [ ] 配置仓库，测试仓库的真实构建与推送成功
- [ ] 纳管集群，向测试命名空间发布成功（策略要求审批时完成审批）
- [ ] `swrctl restart` 后数据仍在
- [ ] `swrctl backup` 成功，`swrctl backups` 能看到

补丁：

- [ ] 预检输出的迁移与 SQL 数量与补丁说明一致
- [ ] `swrctl status` 显示新版本与补丁，`swrctl verify` 通过
- [ ] 补丁修复的功能验证通过；`patch_scripts` 中有本次脚本的记录

构建机上的自动验收见 [14.2](#142-验收脚本)。

## 13. 安全要求

- `config/swr-builder.env`、`config/postgres.env`、`data/secret.key` 和备份都是敏感信息：权限 600 / 700，不要发给无关人员，不要放进工单或聊天。
- 数据库只在 Docker 网络内可访问，不要把 5432 映射到宿主机。
- 平台挂载了宿主机 Docker socket，能控制宿主机上的容器，权限接近 root：限制平台管理员账号，平台只对内网开放，生产环境通过 HTTPS 访问。
- SHA256 只能发现传输损坏，不能证明来源：通过可信渠道获取交付物，并通过另一渠道核对 `.sha256`。
- 补丁 SQL 以平台数据库账号执行：应用前阅读补丁的 `README.md` 与 `sql/`（`tar -tzf` 查看清单，`tar -xzOf <包> sql/dml/001_xxx.sql` 查看内容）。

## 14. 制作交付物（联网构建机）

### 14.1 全量离线包

构建机需要 Docker（含 buildx）、Python 3.11+、Node 22，能访问 Docker Hub、PyPI、npm 仓库与 download.docker.com。ARM64 构建机可以直接执行；x86 构建机先安装 ARM64 binfmt（只在构建机上，不在目标主机上）：

```sh
docker run --privileged --rm \
  tonistiigi/binfmt@sha256:400a4873b838d1b89194d982c45e5fb3cda4593fbfd7e08a02e76b03b21166f0 \
  --install arm64
docker buildx inspect
```

```sh
bash scripts/build-arm64-bundle.sh 2.0.0
# 带铺底资源（sql/、images/、certs/，见 deploy/offline/seed/README.md）：
SWR_SEED_DIR=/path/to/seed bash scripts/build-arm64-bundle.sh 2.0.0
```

版本默认取 `swr_builder/__init__.py` 的 `__version__`。输出在 `artifacts/dist/`（可用 `SWR_DIST_DIR` 修改）：`swr-builder-arm64-<版本>.tar.gz`、`.sha256` 和同名目录。脚本依次：

1. 依赖锁跟不上 `deploy/offline/requirements.in` 或 `requirements.txt` 时，用 ARM64 Python 镜像重新解析并生成带哈希的 `requirements.lock`；否则按锁下载 wheel；
2. `npm ci`、`npm run build`，计算运行依赖标识（`scripts/runtime-contract.py`）；
3. 用按摘要固定的基础镜像（`deploy/offline/base-images.json`）构建运行镜像，离线安装 wheel 并核验哈希，检查依赖导入（含 psycopg）；
4. 按摘要拉取 PostgreSQL 镜像，打上平台专用的本地标签（导入目标机时不会覆盖那里已有的 postgres 镜像）；
5. 组包（`scripts/assemble-arm64-bundle.py`），并独立审核（`scripts/audit-arm64-bundle.py`）：文件哈希、两个镜像的平台与层、代码与前端、铺底资源、依赖。

**v2 的第一次构建**：仓库里的 `requirements.lock` 还是 v1 的（没有 psycopg），脚本会自动重新解析。构建成功后，把 `deploy/offline/requirements.lock` 与 `deploy/offline/runtime-lock.json` 提交回仓库；之后的补丁要基于这两个文件制作，才能与现场运行镜像匹配。

依赖变更（修改 `requirements.in`、`requirements.txt`、`web/package-lock.json` 或 `base-images.json`）后，都要制作新的完整包，并重新验收。

### 14.2 验收脚本

在构建机或测试主机上（需要 Docker 与 `sudo -n`），对解开的完整包目录执行：

```sh
B=artifacts/dist/swr-builder-arm64-2.0.0
python3 scripts/verify-arm64-deployment.py $B --report artifacts/arm64/deployment-qa.json
python3 scripts/verify-arm64-runtime-upgrade.py $B artifacts/arm64/deployment-qa.json --report artifacts/arm64/runtime-upgrade-qa.json
python3 scripts/verify-arm64-docker28.py $B --report artifacts/arm64/docker28-qa.json
```

| 脚本 | 验证内容 |
| --- | --- |
| `verify-arm64-deployment.py` | 独立目录、容器名、网络和端口中：安装与内置数据库、登录改密、目标机离线制作并应用“代码 + SQL”补丁、只有 SQL 的补丁只执行一次、回滚、备份与恢复（含数据库）、SQL 失败与启动失败后的自动恢复、损坏包在停机前拒绝、未结束任务阻止重启、端口映射 |
| `verify-arm64-runtime-upgrade.py` | 模拟新增运行依赖：补丁 / 代码包拒绝新的依赖标识；`upgrade.sh` 升级到新运行镜像且数据不变；跨运行镜像回滚 |
| `verify-arm64-docker28.py` | 在隔离的 Docker 28 daemon 中冷导入两个镜像；在原生 ARM64 上启动 PostgreSQL 与平台并访问页面 |

x86 构建机经 QEMU 执行时，只能说明包内程序是 ARM64 且流程可以执行，不能代替目标 ARM64 主机、现场仓库与集群的验收。

### 14.3 预装依赖

准确版本与哈希以完整包中的 `dependencies/requirements.lock` 为准。

| 能力 | 已包含 |
| --- | --- |
| Web / API | FastAPI、Uvicorn、multipart、HTTPX、requests、websockets |
| 数据库 | psycopg（含 libpq）；SQLAlchemy、Alembic |
| YAML / JSON / 配置 | PyYAML、ruamel.yaml、jsonschema、orjson、Jinja2、python-dotenv、pydantic-settings、TOML |
| 认证 / 加密 / 重试 | cryptography、PyJWT、tenacity |
| Kubernetes / SSH | kubernetes、paramiko |
| 华为云 SDK | core / IAM / SWR / CCE / ECS，固定为 3.1.219 |
| 时区 / 计划 / 校验 | tzdata、dateutil、croniter、email-validator |
| 前端构建 | ARM64 Node 22、npm、按 `package-lock.json` 安装的 Vue / Element Plus / TypeScript / Vite |
| 容器 | ARM64 Docker CLI、Buildx、Compose 插件 |

这些库可以被后续代码直接使用，补丁不需要重新打镜像；预装 SDK 不代表对应业务功能已实现或已通过现场验收。需要此清单之外的库时，要制作新的完整包。
