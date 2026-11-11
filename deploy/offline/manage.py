"""在交付镜像内执行的离线部署控制器；宿主机只需要 Docker 和 POSIX shell。"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

from release import create, digest, extract, verify, version_name

CONTRACT_PATH = Path("/opt/swr-runtime/contract")


def run(*args: str, capture=True) -> str:
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE if capture else None)
    if result.returncode:
        raise RuntimeError(f"命令失败（{args[0]} {args[1] if len(args)>1 else ''}）："
                           + (result.stderr.strip() if capture else str(result.returncode)))
    return result.stdout.strip() if capture else ""


def save(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


class Deployment:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.contract = CONTRACT_PATH.read_text().strip()
        self.state_path = self.root / "deploy.json"
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}

    def container(self):
        result = subprocess.run(["docker", "inspect", self.state["container"]],
                                text=True, capture_output=True)
        if result.returncode:
            # 区分不存在与 daemon 失联，避免把故障误判为已停止。
            run("docker", "info", "--format", "{{.ServerVersion}}")
            return None
        info = json.loads(result.stdout)[0]
        if info.get("Config", {}).get("Labels", {}).get("swr.deployment-root") != str(self.root):
            raise ValueError("同名容器不属于此部署目录，拒绝操作")
        return info

    def idle(self):
        database = self.root / "data/builder.db"
        if not database.exists():
            return
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=10) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table, statuses in (("jobs", ("queued", "running")),
                                    ("deployments", ("queued", "running", "applying", "verifying"))):
                if table in tables:
                    count = db.execute(f"SELECT COUNT(*) FROM {table} WHERE status IN "
                                       f"({','.join('?' for _ in statuses)})", statuses).fetchone()[0]
                    if count:
                        raise ValueError(f"存在 {count} 个未结束的 {table} 任务，请完成或在页面取消后重试")

    def stop(self):
        self.idle()
        info = self.container()
        if info and info["State"]["Running"]:
            run("docker", "stop", "--time", "60", self.state["container"])

    def healthy(self):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            info = self.container()
            if not info or not info["State"]["Running"]:
                raise RuntimeError("应用未保持运行，请用 swrctl logs 查看日志")
            health = info["State"].get("Health", {}).get("Status")
            if health == "healthy":
                return
            if health == "unhealthy":
                raise RuntimeError("应用健康检查失败，请用 swrctl logs 查看日志")
            time.sleep(2)
        raise RuntimeError("等待应用就绪超时（180 秒）")

    def start(self):
        if self.container():
            run("docker", "rm", self.state["container"])
        release = self.root / "releases" / version_name(self.state["current"])
        run("docker", "run", "-d", "--name", self.state["container"],
            "--platform", "linux/arm64", "--restart", "unless-stopped",
            "--network", self.state.get("network", "bridge"),
            "--label", f"swr.deployment-root={self.root}",
            "--label", f"swr.release={self.state['current']}",
            "--publish", f"{self.state['bind']}:{self.state['port']}:8080",
            "--mount", f"type=bind,src={release},dst=/opt/swr/app,readonly",
            "--mount", f"type=bind,src={self.root / 'data'},dst=/var/lib/swr",
            "--mount", f"type=bind,src={self.root / 'config'},dst=/etc/swr,readonly",
            "--mount", "type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock",
            "--env-file", str(self.root / "config/swr-builder.env"),
            "--env", "BUILDER_DATA_DIR=/var/lib/swr", "--env", "BUILDER_PORT=8080",
            "--log-opt", "max-size=20m", "--log-opt", "max-file=5",
            self.state["image"])
        self.healthy()

    def point(self, version: str):
        target = self.root / ".current-new"
        target.unlink(missing_ok=True)
        target.symlink_to(Path("releases") / version_name(version), target_is_directory=True)
        target.replace(self.root / "current")

    def verify_release(self, version: str):
        directory = self.root / "releases" / version_name(version)
        manifest = json.loads((directory / "release.json").read_text())
        contract = manifest["runtime_contract"]
        if contract not in self.state.get("runtimes", {}):
            raise ValueError("此代码版本所需的运行镜像未登记，请使用对应完整包的 upgrade.sh")
        return verify(directory, contract)

    def persist(self):
        self.point(self.state["current"])
        save(self.state_path, self.state)
        temporary = self.root / ".runtime-image-new"
        temporary.write_text(self.state["image"] + "\n")
        temporary.replace(self.root / "runtime-image")

    def stage(self, archive: Path):
        with tempfile.TemporaryDirectory(prefix="stage-", dir=self.root / "tmp") as temporary:
            staged = Path(temporary) / "release"
            staged.mkdir()
            extract(archive, staged)
            manifest = verify(staged, self.contract)
            version = manifest["version"]
            destination = self.root / "releases" / version
            if destination.exists():
                if verify(destination, self.contract) != manifest:
                    raise ValueError("已有同版本但内容不同；必须使用新的版本名")
            else:
                staged.rename(destination)
            return version

    def snapshot(self):
        """调用方必须已停止应用，数据库、主密钥、上传、配置一致备份。"""
        info = self.container()
        if info and info["State"]["Running"]:
            raise RuntimeError("备份前应用必须停止")
        stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1000000:06d}"
        archive = self.root / "backups" / f"backup-{stamp}.tar.gz"
        with tempfile.TemporaryDirectory(dir=self.root / "tmp") as temporary:
            metadata = Path(temporary) / "snapshot.json"
            save(metadata, self.state)
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(metadata, arcname="snapshot.json")
                for name in ("config", "data"):
                    for path in (self.root / name).rglob("*"):
                        if path.is_symlink():
                            raise ValueError(f"备份目录含符号链接，拒绝备份：{path}")
                    tar.add(self.root / name, arcname=name)
        archive.chmod(0o600)
        archive.with_suffix(archive.suffix + ".sha256").write_text(digest(archive) + "\n")
        print(f"备份已保存：{archive}", flush=True)
        return archive

    def restore_files(self, archive: Path):
        checksum = archive.with_suffix(archive.suffix + ".sha256")
        if not checksum.exists() or digest(archive) != checksum.read_text().split()[0]:
            raise ValueError("备份 SHA256 不匹配或缺少校验文件")
        with tempfile.TemporaryDirectory(dir=self.root / "tmp") as temporary:
            unpacked = Path(temporary)
            extract(archive, unpacked)
            previous = json.loads((unpacked / "snapshot.json").read_text())
            self.verify_release(previous["current"])
            for name in ("config", "data"):
                if not (unpacked / name).is_dir():
                    raise ValueError("备份缺少配置或数据目录")
            for name in ("config", "data"):
                (self.root / name).rename(unpacked / f"old-{name}")
                (unpacked / name).rename(self.root / name)
                (self.root / name).chmod(0o700)
        return previous

    def change(self, version: str):
        manifest = self.verify_release(version)
        if version == self.state["current"]:
            print("已经是此版本，无需更新")
            return
        self.stop()
        previous = dict(self.state)
        try:
            backup = self.snapshot()
        except Exception:
            self.start()
            raise
        self.state.update(current=version, previous=previous["current"], last_backup=str(backup),
                          image=self.state["runtimes"][manifest["runtime_contract"]])
        try:
            self.start()
            self.persist()
        except Exception:
            info = self.container()
            if info:
                run("docker", "rm", "-f", self.state["container"])
            self.restore_files(backup)
            self.state = previous
            self.persist()
            self.start()
            print("新版本启动失败，已恢复更新前的代码、配置和数据", file=sys.stderr)
            raise
        print(f"已切换到 {version}，健康检查通过")

    def install(self, args):
        if self.state:
            raise ValueError("已部署，请使用 update")
        if (self.root / "data").exists() and any((self.root / "data").iterdir()):
            raise ValueError("检测到已有数据，拒绝覆盖；请按文档迁移")
        if (self.root / "config").exists() and any((self.root / "config").iterdir()):
            raise ValueError("检测到已有配置，拒绝覆盖；请按文档迁移")
        if not 1 <= args.port <= 65535:
            raise ValueError("端口范围为 1–65535")
        self.state = {"schema": 1, "image": args.image, "container": args.name,
                      "bind": args.bind, "port": args.port, "current": "", "previous": None,
                      "network": args.network, "runtimes": {self.contract: args.image}}
        if self.container():
            raise ValueError("容器已存在，拒绝重复安装")
        for name in ("config", "data", "releases", "backups", "tmp", "updates", "ops", "source"):
            (self.root / name).mkdir(exist_ok=True, mode=0o700)
        bundle = args.bundle
        for path in (bundle / "ops").iterdir():
            if path.is_file():
                shutil.copy2(path, self.root / "ops" / path.name)
        shutil.copy2(bundle / "swrctl", self.root / "swrctl")
        (self.root / "swrctl").chmod(0o755)
        shutil.copy2(bundle / "config.yaml", self.root / "config/config.yaml")
        shutil.copy2(bundle / "swr-builder.env.example", self.root / "config/swr-builder.env")
        (self.root / "config/swr-builder.env").chmod(0o600)
        (self.root / "config/certs").mkdir(exist_ok=True)
        (self.root / "runtime-image").write_text(args.image + "\n")
        for path in (bundle / "releases").glob("*.tar.gz"):
            self.state["current"] = self.stage(path)
        if not self.state["current"]:
            raise ValueError("完整包缺少代码发布包")
        extract(bundle / "source.tar.gz", self.root / "source")
        shutil.copy2(bundle / "部署文档.md", self.root / "部署文档.md")
        save(self.state_path, self.state)
        self.point(self.state["current"])
        self.start()
        print(f"安装成功：http://<服务器IP>:{args.port}；管理入口：{self.root}/swrctl")
        print(f"首次管理员密码文件：{self.root}/data/admin-initial-password.txt（请在服务器本地读取）")

    def upgrade(self, args):
        self.state.setdefault("runtimes", {})[self.contract] = args.image
        archives = list((args.bundle / "releases").glob("*.tar.gz"))
        if len(archives) != 1:
            raise ValueError("完整包必须包含且仅包含一个代码发布包")
        version = self.stage(archives[0])
        source = self.root / "source-versions" / version
        if source.exists():
            raise ValueError("此完整包的源码已安装，请使用新的发布版本")
        source.mkdir(parents=True)
        extract(args.bundle / "source.tar.gz", source)
        self.change(version)
        for path in (args.bundle / "ops").iterdir():
            if path.is_file():
                shutil.copy2(path, self.root / "ops" / path.name)
        shutil.copy2(args.bundle / "swrctl", self.root / "swrctl")
        (self.root / "swrctl").chmod(0o755)
        shutil.copy2(args.bundle / "部署文档.md", self.root / "部署文档.md")
        print(f"运行镜像已更新，配置和数据保留；新版源码：{source}")

    def build_update(self, source: Path, version: str):
        source = source.resolve()
        if not source.is_relative_to(self.root):
            raise ValueError("请先将源码放在部署目录内（默认 /data/swr/source）")
        if digest(source / "web/package-lock.json") != digest(Path("/opt/swr-web-deps/package-lock.json")):
            raise ValueError("前端依赖已变化，请用联网构建机生成新版完整镜像包")
        if digest(source / "deploy/offline/requirements.lock") != digest(Path("/opt/swr-runtime/requirements.lock")):
            raise ValueError("Python 依赖锁已变化，请用联网构建机生成新版完整镜像包")
        if digest(source / "deploy/offline/runtime-lock.json") != self.contract:
            raise ValueError("运行环境标识已变化，请使用匹配的完整镜像包")
        with tempfile.TemporaryDirectory(prefix="swr-code-build-") as temporary:
            workspace = Path(temporary)
            shutil.copytree(source / "swr_builder", workspace / "swr_builder",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "webui"))
            shutil.copytree(source / "web", workspace / "web",
                            ignore=shutil.ignore_patterns("node_modules", "dist", ".env", ".env.*"))
            (workspace / "web/node_modules").symlink_to("/opt/swr-web-deps/node_modules")
            # npm run build 只运行本地编译工具；无需 npm install，不会访问 npm 仓库。
            run("npm", "--prefix", str(workspace / "web"), "run", "build", capture=False)
            archive = create(workspace, self.root / "updates", version, self.contract)
        print(f"代码更新包：{archive}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    install = sub.add_parser("install")
    install.add_argument("--bundle", type=Path, required=True)
    install.add_argument("--image", required=True)
    install.add_argument("--port", type=int, default=8082)
    install.add_argument("--name", default="swr-builder")
    install.add_argument("--bind", default="0.0.0.0")
    install.add_argument("--network", default="bridge")
    upgrade = sub.add_parser("upgrade")
    upgrade.add_argument("--bundle", type=Path, required=True)
    upgrade.add_argument("--image", required=True)
    for command in ("status", "start", "stop", "restart", "backup", "verify"):
        sub.add_parser(command)
    logs = sub.add_parser("logs")
    logs.add_argument("--tail", type=int, default=100)
    update = sub.add_parser("update")
    update.add_argument("archive", type=Path)
    rollback = sub.add_parser("rollback")
    rollback.add_argument("version", nargs="?")
    restore = sub.add_parser("restore")
    restore.add_argument("archive", type=Path)
    build = sub.add_parser("build-update")
    build.add_argument("source", type=Path)
    build.add_argument("version")
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    os.umask(0o077)
    with (args.root / ".swrctl.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("已有安装、更新或管理命令正在执行")
        deployment = Deployment(args.root)
        if args.command == "install":
            deployment.install(args)
            return
        if not deployment.state:
            raise ValueError("此目录尚未安装")
        if args.command == "upgrade":
            deployment.upgrade(args)
        elif args.command == "status":
            info = deployment.container()
            print(json.dumps({"version": deployment.state["current"],
                              "image": deployment.state["image"],
                              "port": deployment.state["port"],
                              "container_state": info["State"]["Status"] if info else "absent",
                              "health": info["State"].get("Health", {}).get("Status") if info else None},
                             indent=2, ensure_ascii=False))
        elif args.command == "logs":
            run("docker", "logs", "--tail", str(args.tail), deployment.state["container"], capture=False)
        elif args.command == "verify":
            deployment.verify_release(deployment.state["current"])
            run("python", "/opt/swr-runtime/check_dependencies.py", capture=False)
            deployment.healthy()
            print("代码完整性、ARM64 依赖及应用健康检查均通过")
        elif args.command in ("start", "restart"):
            deployment.stop()
            deployment.start()
        elif args.command == "stop":
            deployment.stop()
        elif args.command == "backup":
            info = deployment.container()
            running = bool(info and info["State"]["Running"])
            deployment.stop()
            try:
                deployment.snapshot()
            finally:
                if running:
                    deployment.start()
        elif args.command == "update":
            deployment.change(deployment.stage(args.archive.resolve()))
        elif args.command == "rollback":
            version = args.version or deployment.state.get("previous")
            if not version:
                raise ValueError("没有上一版本，请显式指定 releases 中的版本")
            deployment.change(version)
        elif args.command == "restore":
            deployment.stop()
            try:
                safety = deployment.snapshot()
            except Exception:
                deployment.start()
                raise
            previous = dict(deployment.state)
            try:
                state = deployment.restore_files(args.archive.resolve())
                deployment.state["current"] = state["current"]
                deployment.state["image"] = state["image"]
                deployment.start()
                deployment.persist()
            except Exception:
                info = deployment.container()
                if info:
                    run("docker", "rm", "-f", deployment.state["container"])
                deployment.restore_files(safety)
                deployment.state = previous
                deployment.start()
                raise
            print("已恢复备份中的代码版本、配置及数据")
        elif args.command == "build-update":
            deployment.build_update(args.source, args.version)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, tarfile.TarError) as exc:
        print(f"操作失败：{exc}", file=sys.stderr)
        sys.exit(1)
