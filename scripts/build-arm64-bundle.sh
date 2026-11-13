#!/bin/bash
# 联网构建机使用：生成 ARM64 全量离线包（运行镜像 + PostgreSQL 镜像 + 代码 + 脚本 + 铺底资源）。
# 用法：bash scripts/build-arm64-bundle.sh [版本，默认取 swr_builder/__init__.py 的 __version__]
#   SWR_SEED_DIR=<目录>   铺底资源：sql/、images/、certs/（见 deploy/offline/seed/README.md）
#   SWR_DIST_DIR=<目录>   输出目录，默认 artifacts/dist
# 只改代码 / SQL 时不需要完整包，用 scripts/build-arm64-patch.py 制作补丁包。
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
VERSION=${1:-$(python3 -c 'import re, pathlib; print(re.search(r"__version__ = \"([^\"]+)\"", pathlib.Path("swr_builder/__init__.py").read_text()).group(1))')}
DIST=${SWR_DIST_DIR:-$ROOT/artifacts/dist}
IMAGE="swr-builder:arm64-$VERSION"
base() { python3 -c 'import json, sys; print(json.load(open("deploy/offline/base-images.json"))[sys.argv[1]])' "$1"; }
PYTHON_IMAGE=$(base python)
NODE_IMAGE=$(base node)
DOCKER_IMAGE=$(base docker)
POSTGRES_REF=$(base postgres)
# 平台专用的本地标签，导入目标机时不会覆盖那里已有的同名 postgres 镜像
POSTGRES_TAG="swr-postgres:$(echo "${POSTGRES_REF%%@*}" | cut -d: -f2)-arm64"
mkdir -p artifacts/arm64/wheelhouse artifacts/arm64/vendor
docker run --rm --platform linux/arm64 "$PYTHON_IMAGE" python -c 'import platform; assert platform.machine() == "aarch64"'

# Python 依赖：requirements.in / requirements.txt 变化后（例如 v2 新增 psycopg）重新解析并生成带哈希的锁
if python3 scripts/lock-arm64-wheels.py --check; then
  docker run --rm --platform linux/arm64 -v "$ROOT:/src" -w /src "$PYTHON_IMAGE" \
    python -m pip download --only-binary=:all: --require-hashes \
    -r deploy/offline/requirements.lock -d artifacts/arm64/wheelhouse
else
  STALE="artifacts/arm64/stale-$(date +%Y%m%d%H%M%S)"
  mkdir -p "$STALE"
  if [ -f deploy/offline/requirements.lock ]; then mv deploy/offline/requirements.lock "$STALE/"; fi
  mv artifacts/arm64/wheelhouse "$STALE/"
  mkdir -p artifacts/arm64/wheelhouse
  docker run --rm --platform linux/arm64 -v "$ROOT:/src" -w /src "$PYTHON_IMAGE" \
    python -m pip download --only-binary=:all: -r deploy/offline/requirements.in -d artifacts/arm64/wheelhouse
  python3 scripts/lock-arm64-wheels.py
  echo "已重新生成 deploy/offline/requirements.lock（旧文件在 $STALE）；构建成功后请把它与 runtime-lock.json 提交回仓库" >&2
fi
npm --prefix web ci --no-audit --no-fund
npm --prefix web run build
python3 scripts/runtime-contract.py
python3 - <<'PY'
import shutil
from pathlib import Path
import urllib.request
out = Path('artifacts/arm64/webui')
if out.exists():
    shutil.rmtree(out)
shutil.copytree('swr_builder/webui', out)
vendor = Path('artifacts/arm64/vendor/docker-28.5.2-aarch64.tgz')
if not vendor.exists():
    urllib.request.urlretrieve('https://download.docker.com/linux/static/stable/aarch64/docker-28.5.2.tgz', vendor)
PY
docker build --platform linux/arm64 -f deploy/offline/Dockerfile \
  --build-arg "PYTHON_IMAGE=$PYTHON_IMAGE" --build-arg "NODE_IMAGE=$NODE_IMAGE" \
  --build-arg "DOCKER_IMAGE=$DOCKER_IMAGE" -t "$IMAGE" .
docker run --rm --platform linux/arm64 --network none --entrypoint python "$IMAGE" /opt/swr-runtime/check_dependencies.py

# 内置数据库：按摘要拉取 ARM64 的 PostgreSQL 镜像
docker pull --platform linux/arm64 "$POSTGRES_REF"
docker tag "postgres@${POSTGRES_REF#*@}" "$POSTGRES_TAG"
docker run --rm --platform linux/arm64 --network none --entrypoint postgres "$POSTGRES_TAG" --version

SEED=()
if [ -n "${SWR_SEED_DIR:-}" ]; then SEED=(--seed "$SWR_SEED_DIR"); fi
python3 scripts/assemble-arm64-bundle.py --image "$IMAGE" --postgres-image "$POSTGRES_TAG" \
  --version "$VERSION" --output "$DIST" ${SEED[@]+"${SEED[@]}"}
python3 scripts/audit-arm64-bundle.py "$DIST/swr-builder-arm64-$VERSION"
