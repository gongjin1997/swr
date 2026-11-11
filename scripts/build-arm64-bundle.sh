#!/bin/bash
# 联网构建机使用；在目标离线机更新代码请用 swrctl build-update。
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
VERSION=${1:-$(date +%Y%m%d)-r1}
IMAGE="swr-builder:arm64-$VERSION"
mkdir -p artifacts/arm64/wheelhouse artifacts/arm64/vendor
PYTHON_IMAGE=$(python3 -c 'import json; print(json.load(open("deploy/offline/base-images.json"))["python"])')
NODE_IMAGE=$(python3 -c 'import json; print(json.load(open("deploy/offline/base-images.json"))["node"])')
DOCKER_IMAGE=$(python3 -c 'import json; print(json.load(open("deploy/offline/base-images.json"))["docker"])')
docker run --rm --platform linux/arm64 "$PYTHON_IMAGE" python -c 'import platform; assert platform.machine() == "aarch64"'
if [ -f deploy/offline/requirements.lock ]; then
  docker run --rm --platform linux/arm64 -v "$ROOT:/src" -w /src "$PYTHON_IMAGE" \
    python -m pip download --only-binary=:all: --require-hashes \
    -r deploy/offline/requirements.lock -d artifacts/arm64/wheelhouse
else
  docker run --rm --platform linux/arm64 -v "$ROOT:/src" -w /src "$PYTHON_IMAGE" \
    python -m pip download --only-binary=:all: -r deploy/offline/requirements.in -d artifacts/arm64/wheelhouse
  python3 scripts/lock-arm64-wheels.py
fi
npm --prefix web ci --no-audit --no-fund
npm --prefix web run build
python3 scripts/runtime-contract.py
python3 - <<'PY'
import shutil
from pathlib import Path
import urllib.request
out=Path('artifacts/arm64/webui')
if out.exists(): shutil.rmtree(out)
shutil.copytree('swr_builder/webui',out)
vendor=Path('artifacts/arm64/vendor/docker-28.5.2-aarch64.tgz')
if not vendor.exists(): urllib.request.urlretrieve('https://download.docker.com/linux/static/stable/aarch64/docker-28.5.2.tgz',vendor)
PY
docker build --platform linux/arm64 -f deploy/offline/Dockerfile \
  --build-arg "PYTHON_IMAGE=$PYTHON_IMAGE" --build-arg "NODE_IMAGE=$NODE_IMAGE" \
  --build-arg "DOCKER_IMAGE=$DOCKER_IMAGE" -t "$IMAGE" .
docker run --rm --platform linux/arm64 --network none --entrypoint python "$IMAGE" /opt/swr-runtime/check_dependencies.py
python3 scripts/assemble-arm64-bundle.py --image "$IMAGE" --version "$VERSION"
python3 scripts/audit-arm64-bundle.py "/data/swr/dist/swr-builder-arm64-$VERSION"
