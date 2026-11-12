# 铺底资源（seed）

完整离线包的 `seed/` 目录放首次部署需要的现场资源。构建完整包时用 `SWR_SEED_DIR=<目录>` 指定来源目录，
`scripts/assemble-arm64-bundle.py` 会校验后复制进包；不指定时包里只有本说明。

```text
<铺底目录>/
  sql/                 铺底 SQL，安装后、平台首次启动前执行一次
    ddl/*.sql          结构（建表、索引……）
    dml/*.sql          数据（字典、预置项目或应用……）
    *.sql              在 ddl/、dml/ 之后执行
  images/*.tar[.gz]    业务构建用的基础镜像（docker save 生成，必须是 linux/arm64），安装时 docker load
  certs/*              CA 证书等，安装时复制到 <部署目录>/config/certs/（容器内 /etc/swr/certs/）
```

## SQL 规则

与补丁包的 `sql/` 相同，执行规则见 `swr_builder/store/sqlscripts.py`：

- 顺序：`ddl/` → `dml/` → 根目录 `*.sql`，同一目录按文件名排序，建议 `001_xxx.sql` 带序号命名；
- 每个脚本一个事务，脚本里不要写 `BEGIN;` / `COMMIT;`；确实要在事务外执行（例如
  `CREATE INDEX CONCURRENTLY`）时，第一行写 `-- swr:no-transaction`；
- 只能是 UTF-8，不支持 psql 元命令（`\c`、`\i` 等）；
- 铺底 SQL 统一记在补丁名 `seed` 下，同一路径只执行一次。以后的完整包可以追加新的脚本（例如
  `dml/002_more.sql`），升级时只执行新增的；**已经发布的脚本不能修改**，内容变化会在停机前被拒绝。
- 平台表结构由程序自己的迁移维护，铺底 SQL 不要修改平台的表结构，也不要插入账号（admin 由平台首次启动时创建）；
- 写入数据请用可重复执行的写法，并且只用 PostgreSQL 与 GaussDB 共有的语法（使用外部 GaussDB 时同样执行），
  例如 `INSERT INTO t(...) SELECT ... WHERE NOT EXISTS (SELECT 1 FROM t WHERE ...)`；不要用 `ON CONFLICT`、`MERGE`。
- 从 v1 迁移时，铺底 SQL 在导入 v1 数据之后执行，注意不要与导入的数据冲突。

## 业务基础镜像

在联网机器上准备，例如：

```sh
docker pull --platform linux/arm64 <仓库>/<基础镜像>:<标签>
docker save <仓库>/<基础镜像>:<标签> | gzip > <铺底目录>/images/<名称>-arm64.tar.gz
```

安装时导入目标主机的 Docker，应用的“基础镜像”填写同样的完整名称即可直接使用本地镜像；也可以导入后
`docker tag` / `docker push` 到现场 SWR。
