#!/bin/sh
# 用新版完整包升级：运行依赖（Python / Node / 基础镜像）、PostgreSQL 小版本或管理工具变化时使用。
# 只改代码、SQL 时用补丁包：swrctl apply <补丁包>，不需要完整包。
# 用法：sudo sh upgrade.sh [部署目录，默认 /data/swr]
# 外部数据库：本次升级会执行迁移时，请 DBA 先备份数据库，再设置 SWR_DB_BACKUP_CONFIRMED=1 执行。
set -eu
umask 077
BUNDLE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=${1:-/data/swr}
fail() { echo "$*" >&2; exit 1; }
[ -f "$ROOT/deploy.json" ] || fail '尚未安装，请先使用 install.sh'
ROOT=$(CDPATH= cd -- "$ROOT" && pwd)
if [ "${SWR_ALLOW_EMULATION:-0}" != 1 ]; then
  ARCH=$(docker info --format '{{.Architecture}}')
  case "$ARCH" in aarch64|arm64) ;; *) fail "目标 Docker 是 $ARCH，需要 ARM64 主机";; esac
fi
echo '校验完整包……'
(cd "$BUNDLE" && sha256sum -c SHA256SUMS >/dev/null) || fail '完整包校验失败（SHA256SUMS），请重新拷贝'
load_image() {
  docker load -i "$BUNDLE/$1" >&2
  NAME=$(cat "$BUNDLE/$2")
  [ "$(docker image inspect "$NAME" --format '{{.Architecture}}')" = arm64 ] || fail "$1 不是 arm64 镜像"
  docker image inspect "$NAME" --format '{{.Id}}'
}
echo '导入运行镜像……'
IMAGE=$(load_image images/swr-builder-arm64.tar.gz runtime-image)
PG_IMAGE=
if [ -f "$BUNDLE/images/postgres-arm64.tar.gz" ]; then
  echo '导入 PostgreSQL 镜像……'
  PG_IMAGE=$(load_image images/postgres-arm64.tar.gz postgres-image)
fi
# 使用新完整包里的控制器（/bundle/ops）完成升级；升级成功后它会替换部署目录中的 ops/ 与 swrctl
set -- run --rm -i --platform linux/arm64 --network none \
  --mount "type=bind,src=$ROOT,dst=$ROOT" \
  --mount "type=bind,src=$BUNDLE,dst=/bundle,readonly" \
  --mount type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock \
  --entrypoint python "$IMAGE" /bundle/ops/manage.py --root "$ROOT" upgrade --bundle /bundle \
  --image "$IMAGE" --postgres-image "$PG_IMAGE"
[ "${SWR_DB_BACKUP_CONFIRMED:-0}" != 1 ] || set -- "$@" --db-backup-confirmed
exec docker "$@"
