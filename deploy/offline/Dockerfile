# 构建时全部 FROM 必须指向 linux/arm64；不要复制 x86 的 node_modules。
ARG NODE_IMAGE=node:22-bookworm-slim
ARG PYTHON_IMAGE=python:3.11-slim-bookworm
ARG DOCKER_IMAGE=docker:28.5.2-cli
FROM ${NODE_IMAGE} AS webdeps
WORKDIR /opt/swr-web-deps
COPY web/package.json web/package-lock.json ./
RUN npm ci --include=optional --no-audit --no-fund && npm cache clean --force

FROM ${DOCKER_IMAGE} AS dockercli
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Shanghai \
    PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    BUILDER_DATA_DIR=/var/lib/swr BUILDER_CONFIG=/etc/swr/config.yaml
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=dockercli /usr/local/libexec/docker/cli-plugins/ /usr/local/libexec/docker/cli-plugins/
COPY --from=webdeps /usr/local/bin/node /usr/local/bin/node
COPY --from=webdeps /usr/local/lib/node_modules/npm/ /usr/local/lib/node_modules/npm/
COPY --from=webdeps /opt/swr-web-deps/ /opt/swr-web-deps/
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
COPY artifacts/arm64/wheelhouse/ /opt/swr-wheelhouse/
COPY deploy/offline/requirements.lock /opt/swr-runtime/requirements.lock
RUN python -m pip install --no-index --find-links=/opt/swr-wheelhouse --require-hashes \
      -r /opt/swr-runtime/requirements.lock && python -m pip check
COPY deploy/offline/check_dependencies.py /opt/swr-runtime/check_dependencies.py
COPY deploy/offline/runtime-lock.json /opt/swr-runtime/runtime-lock.json
RUN python /opt/swr-runtime/check_dependencies.py \
 && sha256sum /opt/swr-runtime/runtime-lock.json | cut -d ' ' -f 1 > /opt/swr-runtime/contract
COPY swr_builder/ /opt/swr/app/swr_builder/
COPY artifacts/arm64/webui/ /opt/swr/app/swr_builder/webui/
COPY deploy/offline/config.yaml /etc/swr/config.yaml
WORKDIR /opt/swr/app
EXPOSE 8080
HEALTHCHECK --interval=5s --timeout=5s --start-period=30s --retries=6 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"]
CMD ["python", "-m", "swr_builder", "-c", "/etc/swr/config.yaml", "--host", "0.0.0.0", "--port", "8080"]
