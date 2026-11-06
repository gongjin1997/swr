# 第一阶段：构建 Vue 前端（运行时镜像不包含 Node）
FROM node:22-alpine AS web
ARG NPM_REGISTRY=https://mirrors.huaweicloud.com/repository/npm/
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
# 有 package-lock.json 时按锁定版本安装；首次构建没有锁文件时退回 npm install
RUN if [ -f package-lock.json ]; then npm ci --registry="${NPM_REGISTRY}"; \
    else npm install --registry="${NPM_REGISTRY}"; fi
COPY web/ ./
# vite.config.ts 把产物输出到 ../swr_builder/webui
RUN mkdir -p /swr_builder && npm run build

# 第二阶段：平台自身的镜像。容器内只带 docker CLI，通过挂载宿主机 /var/run/docker.sock 使用宿主机的 Docker 构建
FROM python:3.11-slim

ARG DOCKER_VERSION=24.0.7
ARG DOCKER_MIRROR=https://mirrors.huaweicloud.com/docker-ce/linux/static/stable
ARG PIP_INDEX_URL=https://mirrors.huaweicloud.com/repository/pypi/simple

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Shanghai

RUN set -eux; \
    arch="$(uname -m)"; \
    python -c "import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], '/tmp/docker.tgz')" \
        "${DOCKER_MIRROR}/${arch}/docker-${DOCKER_VERSION}.tgz"; \
    tar -xzf /tmp/docker.tgz --strip-components=1 -C /usr/local/bin docker/docker; \
    rm -f /tmp/docker.tgz; \
    docker --version

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -i "${PIP_INDEX_URL}" -r requirements.txt
COPY swr_builder ./swr_builder
COPY --from=web /swr_builder/webui ./swr_builder/webui
COPY config.example.yaml ./config.example.yaml

EXPOSE 8080
VOLUME ["/app/data"]
CMD ["python", "-m", "swr_builder", "-c", "/app/config.yaml"]
