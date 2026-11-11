#!/bin/sh
# 仅当运行依赖变化时使用新版完整包升级；普通代码更新使用 swrctl update。
set -eu
BUNDLE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=${1:-/data/swr}
[ -f "$ROOT/deploy.json" ] || { echo '尚未安装，请先使用 install.sh'; exit 1; }
ROOT=$(CDPATH= cd -- "$ROOT" && pwd)
(cd "$BUNDLE" && sha256sum -c SHA256SUMS >/dev/null)
docker load -i "$BUNDLE/images/swr-builder-arm64.tar.gz"
IMAGE=$(cat "$BUNDLE/runtime-image")
[ "$(docker image inspect "$IMAGE" --format '{{.Architecture}}')" = arm64 ] || exit 1
IMAGE=$(docker image inspect "$IMAGE" --format '{{.Id}}')
docker run --rm -i --platform linux/arm64 --network none \
  --mount "type=bind,src=$ROOT,dst=$ROOT" \
  --mount "type=bind,src=$BUNDLE,dst=/bundle,readonly" \
  --mount type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock \
  --entrypoint python "$IMAGE" /bundle/ops/manage.py --root "$ROOT" upgrade \
  --bundle /bundle --image "$IMAGE"
