#!/bin/sh
# 可选：仅用于尚未安装 Docker 的 ARM64 Linux 主机；绝不覆盖已有 Docker。
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
[ "$(id -u)" = 0 ] || { echo '请使用 root / sudo 执行'; exit 1; }
case "$(uname -m)" in aarch64|arm64) ;; *) echo '此包只支持 ARM64'; exit 1;; esac
if command -v docker >/dev/null 2>&1 || command -v dockerd >/dev/null 2>&1; then
  echo '检测到已安装 Docker，拒绝覆盖；请使用现有服务'
  exit 1
fi
command -v systemctl >/dev/null || { echo '需要 systemd，其他 init 系统请手工安装 Docker'; exit 1; }
command -v iptables >/dev/null || { echo '缺少 iptables；请从目标发行版离线介质安装 iptables 后重试'; exit 1; }
[ ! -e /etc/systemd/system/docker.service ] || { echo '已有 docker.service，拒绝覆盖'; exit 1; }
(cd "$HERE/.." && sha256sum -c SHA256SUMS >/dev/null)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT HUP INT TERM
tar -xzf "$HERE/docker-28.5.2-aarch64.tgz" -C "$WORK"
install -m 755 "$WORK"/docker/* /usr/local/bin/
cat > /etc/systemd/system/docker.service <<'UNIT'
[Unit]
Description=Docker Application Container Engine (offline static distribution)
After=network-online.target
Wants=network-online.target
[Service]
Type=notify
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/usr/local/bin/dockerd
ExecReload=/bin/kill -s HUP $MAINPID
Restart=always
RestartSec=5
TimeoutStartSec=0
Delegate=yes
KillMode=process
LimitNOFILE=1048576
TasksMax=infinity
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now docker
docker version
