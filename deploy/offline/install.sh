#!/bin/sh
# 首次全量离线安装：导入运行镜像与 PostgreSQL 镜像，创建配置、数据库与铺底数据，然后启动平台。
# 用法：sudo sh install.sh [部署目录，默认 /data/swr] [访问端口，默认 8082]
# 可选环境变量（详见部署文档）：
#   SWR_DATABASE=external      使用外部 PostgreSQL / GaussDB，不启动内置数据库（必须同时提供 SWR_ENV_FILE）
#   SWR_ENV_FILE=<文件>        预先填写的环境变量（仓库凭证、外部数据库连接等），复制为 config/swr-builder.env
#   SWR_IMPORT_V1=<v1 目录>    导入 v1（SQLite）部署的数据；v1 必须已停止
#   SWR_CONTAINER_NAME / SWR_DB_CONTAINER_NAME / SWR_BIND_ADDRESS / SWR_DOCKER_NETWORK
set -eu
umask 077
BUNDLE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=${1:-/data/swr}
PORT=${2:-8082}
DATABASE=${SWR_DATABASE:-bundled}
fail() { echo "$*" >&2; exit 1; }
check_path() {
  case "$1" in /*) ;; *) fail "$2必须是绝对路径：$1";; esac
  case "$1" in *' '*|*','*|*':'*) fail "$2不能包含空格、逗号或冒号：$1";; esac
}
check_path "$ROOT" 部署目录
case "$PORT" in ''|*[!0-9]*) fail "端口必须是数字：$PORT";; esac
case "$DATABASE" in bundled|external) ;; *) fail 'SWR_DATABASE 只能是 bundled（内置 PostgreSQL）或 external（外部数据库）';; esac
command -v docker >/dev/null || fail '请先安装 Docker：sudo sh tools/install-docker.sh（见部署文档）'
if [ "${SWR_ALLOW_EMULATION:-0}" != 1 ]; then
  ARCH=$(docker info --format '{{.Architecture}}')
  case "$ARCH" in aarch64|arm64) ;; *) fail "目标 Docker 是 $ARCH，需要 ARM64 主机";; esac
fi
[ ! -e "$ROOT/deploy.json" ] || fail '此目录已部署：补丁用 swrctl apply，完整包升级用 upgrade.sh；不要覆盖安装'
echo '校验完整包……'
(cd "$BUNDLE" && sha256sum -c SHA256SUMS >/dev/null) || fail '完整包校验失败（SHA256SUMS），请重新拷贝'

# 导入镜像并输出导入后的镜像 ID（Docker classic / containerd 镜像存储的 ID 形式不同，以目标 daemon 为准）
load_image() {
  docker load -i "$BUNDLE/$1" >&2
  NAME=$(cat "$BUNDLE/$2")
  [ "$(docker image inspect "$NAME" --format '{{.Architecture}}')" = arm64 ] || fail "$1 不是 arm64 镜像"
  docker image inspect "$NAME" --format '{{.Id}}'
}
echo '导入运行镜像……'
IMAGE=$(load_image images/swr-builder-arm64.tar.gz runtime-image)
PG_IMAGE=
if [ "$DATABASE" = bundled ]; then
  echo '导入 PostgreSQL 镜像……'
  PG_IMAGE=$(load_image images/postgres-arm64.tar.gz postgres-image)
fi
mkdir -p "$ROOT"
ROOT=$(CDPATH= cd -- "$ROOT" && pwd)

# 控制器在运行镜像中执行：关闭网络，部署目录按原路径挂载，操作宿主机 Docker
set -- run --rm -i --platform linux/arm64 --network none \
  --mount "type=bind,src=$ROOT,dst=$ROOT" \
  --mount "type=bind,src=$BUNDLE,dst=/bundle,readonly" \
  --mount type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock
if [ -n "${SWR_ENV_FILE:-}" ]; then
  [ -f "$SWR_ENV_FILE" ] || fail "找不到环境文件 $SWR_ENV_FILE"
  ENV_FILE=$(CDPATH= cd -- "$(dirname -- "$SWR_ENV_FILE")" && pwd)/$(basename -- "$SWR_ENV_FILE")
  check_path "$ENV_FILE" 环境文件路径
  set -- "$@" --mount "type=bind,src=$ENV_FILE,dst=/import/swr-builder.env,readonly"
fi
if [ -n "${SWR_IMPORT_V1:-}" ]; then
  [ -d "$SWR_IMPORT_V1" ] || fail "找不到 v1 部署目录 $SWR_IMPORT_V1"
  V1=$(CDPATH= cd -- "$SWR_IMPORT_V1" && pwd)
  check_path "$V1" 'v1 部署目录'
  set -- "$@" --mount "type=bind,src=$V1,dst=$V1,readonly"
fi
set -- "$@" --entrypoint python "$IMAGE" /bundle/ops/manage.py --root "$ROOT" install --bundle /bundle \
  --image "$IMAGE" --postgres-image "$PG_IMAGE" --database "$DATABASE" --port "$PORT" \
  --name "${SWR_CONTAINER_NAME:-swr-builder}" --db-name "${SWR_DB_CONTAINER_NAME:-swr-postgres}" \
  --bind "${SWR_BIND_ADDRESS:-0.0.0.0}"
[ -z "${SWR_ENV_FILE:-}" ] || set -- "$@" --env-file /import/swr-builder.env
[ -z "${SWR_IMPORT_V1:-}" ] || set -- "$@" --import-v1 "$V1"
[ -z "${SWR_DOCKER_NETWORK:-}" ] || set -- "$@" --network "$SWR_DOCKER_NETWORK"
exec docker "$@"
