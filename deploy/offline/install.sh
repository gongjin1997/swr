#!/bin/sh
set -eu
umask 077
BUNDLE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=${1:-/data/swr}
PORT=${2:-8082}
case "$ROOT" in /*) ;; *) echo '部署目录必须是绝对路径' >&2; exit 1;; esac
case "$ROOT" in *','*|*':'*|*' '*|*'\n'*) echo '部署路径不能包含空格、逗号或冒号' >&2; exit 1;; esac
command -v docker >/dev/null || { echo '请先安装 Docker，离线安装脚本：tools/install-docker.sh'; exit 1; }
if [ "${SWR_ALLOW_EMULATION:-0}" != 1 ]; then
  ARCH=$(docker info --format '{{.Architecture}}')
  case "$ARCH" in aarch64|arm64) ;; *) echo "目标 Docker 是 $ARCH，需要 ARM64 主机" >&2; exit 1;; esac
fi
if [ -e "$ROOT/deploy.json" ]; then
  echo '此目录已部署，请使用 swrctl update 更新代码；不要覆盖安装' >&2
  exit 1
fi
(cd "$BUNDLE" && sha256sum -c SHA256SUMS >/dev/null)
docker load -i "$BUNDLE/images/swr-builder-arm64.tar.gz"
IMAGE=$(cat "$BUNDLE/runtime-image")
ARCH=$(docker image inspect "$IMAGE" --format '{{.Architecture}}')
[ "$ARCH" = arm64 ] || { echo '包内镜像不是 arm64' >&2; exit 1; }
# Docker classic / containerd 镜像存储的 ID 形式不同，使用目标 daemon 导入后的实际 ID。
IMAGE=$(docker image inspect "$IMAGE" --format '{{.Id}}')
mkdir -p "$ROOT"
ROOT=$(CDPATH= cd -- "$ROOT" && pwd)
docker run --rm -i --platform linux/arm64 --network none \
  --mount "type=bind,src=$ROOT,dst=$ROOT" \
  --mount "type=bind,src=$BUNDLE,dst=/bundle,readonly" \
  --mount type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock \
  --entrypoint python "$IMAGE" /bundle/ops/manage.py --root "$ROOT" install \
  --bundle /bundle --port "$PORT" --image "$IMAGE" \
  --name "${SWR_CONTAINER_NAME:-swr-builder}" --bind "${SWR_BIND_ADDRESS:-0.0.0.0}" \
  --network "${SWR_DOCKER_NETWORK:-bridge}"
