# 平台自身的镜像：容器内只带 docker CLI，通过挂载宿主机 /var/run/docker.sock 使用宿主机的 Docker 构建
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
COPY config.example.yaml ./config.example.yaml

EXPOSE 8080
VOLUME ["/app/data"]
CMD ["python", "-m", "swr_builder", "-c", "/app/config.yaml"]
