"""在交付镜像内执行的离线部署控制器；宿主机只需要 Docker 和 POSIX shell。

install.sh / upgrade.sh / swrctl 在运行镜像中启动本程序：关闭网络，部署目录按宿主机原路径挂载，并挂载宿主机
Docker socket；所有容器操作都由宿主机 Docker 完成。数据库命令（迁移、补丁 SQL、导入、状态）在一次性容器里用
对应版本的代码执行（python -m swr_builder db ...），与应用使用相同的网络、配置和数据目录。

部署目录（默认 /data/swr）：
    deploy.json            部署记录（schema 2）：代码版本、运行镜像、端口、数据库、已应用的补丁
    history.log            安装、补丁、升级、回滚、恢复的记录（JSON Lines，只追加）
    current -> releases/<版本>
    releases/<版本>/       不可变的代码与前端产物（逐文件 SHA256）
    patches/<版本>/        已应用补丁的 patch.json、README.md 与 sql/，供审计
    config/                config.yaml、swr-builder.env、postgres.env、certs/（应用容器内只读挂载为 /etc/swr）
    data/                  平台数据：secret.key、构建与发布日志、上传文件（应用容器内 /var/lib/swr）
    pgdata/                内置 PostgreSQL 的数据目录（使用外部数据库时没有）
    backups/backup-<时间>/ 停机一致备份：database.dump、files.tar.gz、snapshot.json、SHA256SUMS
    updates/               待应用的补丁包，以及 build-patch 的输出
    source/                首次交付的源码；source-versions/<版本>/ 是完整包升级带来的新版源码
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import json
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from release import (CODE_MANIFEST, PATCH_MANIFEST, create_patch, digest, extract, verify, verify_patch,
                     version_name)

CONTRACT_PATH = Path("/opt/swr-runtime/contract")
APP_PORT = 8080
APP_CODE, APP_DATA, APP_CONFIG = "/opt/swr/app", "/var/lib/swr", "/etc/swr"
DB_USER = DB_NAME = "swr"
PG_DATA = "/var/lib/postgresql/data"
SEED_PATCH = "seed"
RESERVED_NETWORKS = ("bridge", "host", "none")
DIRECTORIES = ("config", "data", "releases", "patches", "backups", "updates", "tmp", "ops", "source")
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}")
ENV_LINE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
DATABASE_ENV = ("BUILDER_DATABASE_URL", "BUILDER_DATABASE_PASSWORD", "BUILDER_DATABASE_DRIVER")
SQLITE_FILES = ("builder.db", "builder.db-wal", "builder.db-shm", "builder.db-journal")
MANAGED_BEGIN = "# ---- 内置 PostgreSQL 连接：由 swrctl 生成，不要修改 ----"


def _tail(text: str, lines: int = 20) -> str:
    return "\n".join((text or "").strip().splitlines()[-lines:])


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1000000:06d}"


def save(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def read_env(path: Path) -> List[str]:
    """读取 docker --env-file 格式（NAME=value，# 开头为注释）；拒绝其他写法，避免 Docker 把它当成要透传的变量名。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    for number, line in enumerate(lines, 1):
        text = line.strip()
        if text and not text.startswith("#") and not ENV_LINE.fullmatch(text):
            raise ValueError(f"{path} 第 {number} 行不是 NAME=value 格式（不要写 export、引号外的空格或续行）")
    return lines


def env_values(lines: Sequence[str]) -> Dict[str, str]:
    values = {}
    for line in lines:
        text = line.strip()
        if text and not text.startswith("#"):
            key, _, value = text.partition("=")
            values[key] = value
    return values


def write_private(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def checksums(folder: Path) -> str:
    return "".join(f"{digest(p)}  {p.relative_to(folder).as_posix()}\n"
                   for p in sorted(folder.rglob("*")) if p.is_file() and p.name != "SHA256SUMS")


class Docker:
    """宿主机 Docker CLI 的封装；控制器只通过这些方法操作容器，测试用假实现替换。"""

    def __init__(self, binary: str = "docker"):
        self.binary = binary

    def call(self, *args: str) -> str:
        result = subprocess.run([self.binary, *args], text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError(f"docker {args[0]} 失败：{_tail(result.stderr or result.stdout)}")
        return result.stdout.strip()

    def info(self) -> str:
        return self.call("info", "--format", "{{.ServerVersion}}")

    def _inspect(self, kind: str, name: str) -> Optional[dict]:
        result = subprocess.run([self.binary, kind, "inspect", name], text=True, capture_output=True)
        if result.returncode:
            self.info()  # 区分“不存在”与 daemon 失联，避免把故障误判为已停止
            return None
        return json.loads(result.stdout)[0]

    def container(self, name: str) -> Optional[dict]:
        return self._inspect("container", name)

    def image(self, ref: str) -> Optional[dict]:
        return self._inspect("image", ref)

    def network(self, name: str) -> Optional[dict]:
        return self._inspect("network", name)

    def create_network(self, name: str, labels: Dict[str, str]) -> None:
        args = ["network", "create", "--driver", "bridge"]
        for key, value in labels.items():
            args += ["--label", f"{key}={value}"]
        self.call(*args, name)

    def run_service(self, name: str, image: str, options: List[str], command: Sequence[str] = ()) -> None:
        self.call("run", "-d", "--name", name, *options, image, *command)

    def run_task(self, image: str, options: List[str], command: List[str], stream: bool = False) -> str:
        """docker run --rm；stream=True 时输出直接显示给运维（迁移、SQL 的进度），只看退出码。"""
        args = [self.binary, "run", "--rm", *options, image, *command]
        label = " ".join(command[3:5])
        if stream:
            result = subprocess.run(args)
            if result.returncode:
                raise RuntimeError(f"{label} 失败（退出码 {result.returncode}），原因见上方输出")
            return ""
        result = subprocess.run(args, text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError(f"{label} 失败：{_tail(result.stderr or result.stdout)}")
        return result.stdout

    def start(self, name: str) -> None:
        self.call("start", name)

    def stop(self, name: str, timeout: int) -> None:
        self.call("stop", "--time", str(timeout), name)

    def remove(self, name: str) -> None:
        self.call("rm", "-f", name)

    def exec(self, name: str, command: List[str], stdin_path: Optional[Path] = None,
             stdout_path: Optional[Path] = None) -> str:
        args = [self.binary, "exec", *(["-i"] if stdin_path else []), name, *command]
        with (stdin_path.open("rb") if stdin_path else nullcontext()) as source, \
                (stdout_path.open("wb") if stdout_path else nullcontext()) as target:
            result = subprocess.run(args, stdin=source, stdout=target if target else subprocess.PIPE,
                                    stderr=subprocess.PIPE)
        if result.returncode:
            raise RuntimeError(f"{command[0]} 失败：{_tail(result.stderr.decode(errors='replace'))}")
        return "" if stdout_path else result.stdout.decode(errors="replace").strip()

    def load(self, path: Path) -> str:
        return self.call("load", "-i", str(path))

    def logs(self, name: str, tail: int) -> None:
        subprocess.run([self.binary, "logs", "--tail", str(tail), name])


class Deployment:
    def __init__(self, root: Path, docker: Optional[Docker] = None, contract: Optional[str] = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.root = root.resolve()
        self.docker = docker or Docker()
        self.contract = contract if contract is not None else CONTRACT_PATH.read_text().strip()
        self.sleep = sleep
        self.state_path = self.root / "deploy.json"
        self.state: dict = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        if self.state and self.state.get("schema") != 2:
            raise ValueError("此目录是 v1 的部署（SQLite）；v2 请安装到新目录并导入旧数据，见部署文档「从 v1 迁移」")

    # ------------------------------------------------------------ 基本信息
    @property
    def bundled(self) -> bool:
        return self.state["database"]["mode"] == "bundled"

    @property
    def app_name(self) -> str:
        return self.state["container"]

    @property
    def db_name(self) -> str:
        return self.state["database"]["container"]

    def release_dir(self, version: str) -> Path:
        return self.root / "releases" / version_name(version)

    def contract_of(self, code: Path) -> str:
        return json.loads((code / CODE_MANIFEST).read_text())["runtime_contract"]

    def runtime_for(self, contract: str) -> str:
        image = self.state.get("runtimes", {}).get(contract)
        if not image:
            raise ValueError("此代码需要的运行镜像未安装（运行依赖已变化）；请先用对应完整包的 upgrade.sh 升级")
        if not self.docker.image(image):
            raise ValueError(f"运行镜像 {image[:19]} 已从 Docker 中删除；请重新导入对应完整包的镜像")
        return image

    def verify_release(self, version: str) -> dict:
        directory = self.release_dir(version)
        if not (directory / CODE_MANIFEST).is_file():
            raise ValueError(f"releases/ 中没有版本 {version}")
        contract = self.contract_of(directory)
        self.runtime_for(contract)
        return verify(directory, contract)

    def history(self, action: str, version: str, result: str = "ok", **detail) -> None:
        entry = {"at": now(), "action": action, "version": version, "result": result, **detail}
        with (self.root / "history.log").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _owned(self, info: Optional[dict], name: str) -> Optional[dict]:
        if info and info.get("Config", {}).get("Labels", {}).get("swr.deployment-root") != str(self.root):
            raise ValueError(f"同名容器 {name} 不属于此部署目录，拒绝操作")
        return info

    def app_info(self) -> Optional[dict]:
        return self._owned(self.docker.container(self.app_name), self.app_name)

    def db_info(self) -> Optional[dict]:
        return self._owned(self.docker.container(self.db_name), self.db_name) if self.bundled else None

    def running(self, info: Optional[dict]) -> bool:
        return bool(info and info["State"]["Running"])

    # ------------------------------------------------------------ 容器
    def wait_healthy(self, name: str, timeout: int, label: str) -> None:
        logs = f"swrctl logs{' --db' if label == '数据库' else ''}"
        deadline = time.monotonic() + timeout
        restarts = None
        while time.monotonic() < deadline:
            info = self.docker.container(name)
            if not self.running(info):
                raise RuntimeError(f"{label}容器未保持运行，请用 {logs} 查看日志")
            count = info.get("RestartCount", 0)
            restarts = count if restarts is None else restarts
            if count > restarts:
                # 等待期间进程退出、又被 --restart 拉起：不必等到超时
                raise RuntimeError(f"{label}进程启动后退出（已被自动重启），请用 {logs} 查看日志")
            health = info["State"].get("Health", {}).get("Status")
            if health == "healthy":
                return
            if health == "unhealthy":
                raise RuntimeError(f"{label}健康检查失败，请用 swrctl logs{' --db' if label == '数据库' else ''} 查看日志")
            self.sleep(2)
        raise RuntimeError(f"等待{label}就绪超时（{timeout} 秒）")

    def ensure_network(self) -> None:
        network = self.state["network"]
        if network in RESERVED_NETWORKS:
            return
        info = self.docker.network(network)
        if info is None:
            self.docker.create_network(network, {"swr.deployment-root": str(self.root)})
        elif info.get("Driver") not in (None, "bridge"):
            raise ValueError(f"Docker 网络 {network} 不是 bridge 网络")

    def run_database(self, image: str) -> None:
        self.ensure_network()
        options = ["--platform", "linux/arm64", "--restart", "unless-stopped", "--network", self.state["network"],
                   "--label", f"swr.deployment-root={self.root}", "--label", "swr.role=database",
                   "--env-file", str(self.root / "config/postgres.env"),
                   "--mount", f"type=bind,src={self.root / 'pgdata'},dst={PG_DATA}",
                   "--shm-size", "256m",
                   # 用 TCP 检查：首次初始化时临时服务只监听 socket，不能把它当成就绪
                   "--health-cmd", f"pg_isready -h 127.0.0.1 -U {DB_USER} -d {DB_NAME}",
                   "--health-interval", "5s", "--health-timeout", "5s", "--health-start-period", "60s",
                   "--health-retries", "12", "--log-opt", "max-size=20m", "--log-opt", "max-file=5"]
        self.docker.run_service(self.db_name, image, options)

    def start_database(self) -> None:
        if not self.bundled:
            return
        info = self.db_info()
        if info is None:
            self.run_database(self.state["database"]["image"])
        elif not self.running(info):
            self.docker.start(self.db_name)
        self.wait_healthy(self.db_name, 180, "数据库")

    def stop_database(self) -> None:
        if self.running(self.db_info()):
            self.docker.stop(self.db_name, 120)

    def replace_database(self, image: str) -> None:
        """同一主版本内更换 PostgreSQL 镜像（小版本升级），数据目录不变。"""
        if self.db_info():
            self.docker.stop(self.db_name, 120)
            self.docker.remove(self.db_name)
        self.run_database(image)
        self.wait_healthy(self.db_name, 180, "数据库")
        self.state["database"]["image"] = image

    def remove_app(self) -> None:
        if self.app_info():
            self.docker.remove(self.app_name)

    def start_app(self) -> None:
        """每次都重建应用容器，保证挂载的是当前版本的代码。"""
        self.start_database()
        self.remove_app()
        host_network = self.state["network"] == "host"
        port = int(self.state["port"]) if host_network else APP_PORT
        # host 网络下平台直接监听宿主机地址：只监听某个地址时，健康检查也要访问该地址
        probe = self.state["bind"] if host_network and self.state["bind"] not in ("0.0.0.0", "::", "") else "127.0.0.1"
        options = ["--platform", "linux/arm64", "--restart", "unless-stopped", "--network", self.state["network"],
                   "--label", f"swr.deployment-root={self.root}", "--label", "swr.role=app",
                   "--label", f"swr.release={self.state['current']}"]
        if not host_network:
            options += ["--publish", f"{self.state['bind']}:{self.state['port']}:{APP_PORT}"]
        options += [
            "--mount", f"type=bind,src={self.release_dir(self.state['current'])},dst={APP_CODE},readonly",
            "--mount", f"type=bind,src={self.root / 'data'},dst={APP_DATA}",
            "--mount", f"type=bind,src={self.root / 'config'},dst={APP_CONFIG},readonly",
            "--mount", "type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock",
            "--env-file", str(self.root / "config/swr-builder.env"),
            "--env", f"BUILDER_DATA_DIR={APP_DATA}", "--env", f"BUILDER_CONFIG={APP_CONFIG}/config.yaml",
            "--health-cmd", "python -c \"import urllib.request; "
                            f"urllib.request.urlopen('http://{probe}:{port}/healthz', timeout=3)\"",
            "--health-interval", "5s", "--health-timeout", "5s", "--health-start-period", "30s",
            "--health-retries", "6", "--log-opt", "max-size=20m", "--log-opt", "max-file=5"]
        listen = ["--host", self.state["bind"] if host_network else "0.0.0.0", "--port", str(port)]
        self.docker.run_service(self.app_name, self.state["image"], options,
                                ["python", "-m", "swr_builder", "-c", f"{APP_CONFIG}/config.yaml", *listen])
        self.wait_healthy(self.app_name, 180, "应用")

    def stop_app(self) -> None:
        if self.running(self.app_info()):
            self.docker.stop(self.app_name, 60)

    # ------------------------------------------------------------ 数据库命令（一次性容器）
    def task(self, argv: List[str], *, code: Optional[Path] = None, image: Optional[str] = None,
             mounts: Sequence[Tuple[Path, str]] = (), writable: bool = False, stream: bool = False) -> str:
        """在一次性容器中运行 python -m swr_builder db <argv>；mounts 额外挂载的目录默认只读。"""
        code = code or self.release_dir(self.state["current"])
        options = ["--platform", "linux/arm64", "--network", self.state["network"],
                   "--label", f"swr.deployment-root={self.root}", "--label", "swr.role=task",
                   "--env-file", str(self.root / "config/swr-builder.env"),
                   "--env", f"BUILDER_DATA_DIR={APP_DATA}", "--env", f"BUILDER_CONFIG={APP_CONFIG}/config.yaml",
                   "--mount", f"type=bind,src={code},dst={APP_CODE},readonly",
                   "--mount", f"type=bind,src={self.root / 'data'},dst={APP_DATA}",
                   "--mount", f"type=bind,src={self.root / 'config'},dst={APP_CONFIG},readonly",
                   "--workdir", APP_CODE]
        for source, target in mounts:
            options += ["--mount", f"type=bind,src={source},dst={target}" + ("" if writable else ",readonly")]
        return self.docker.run_task(image or self.state["image"], options,
                                    ["python", "-m", "swr_builder", "db", *argv], stream=stream)

    def _json(self, output: str) -> dict:
        lines = [line for line in output.strip().splitlines() if line.startswith("{")]
        if not lines:
            raise RuntimeError(f"数据库命令没有返回结果：{_tail(output, 5)}")
        return json.loads(lines[-1])

    def db_status(self, code: Optional[Path] = None, image: Optional[str] = None) -> dict:
        return self._json(self.task(["status", "--json"], code=code, image=image))

    def ensure_idle(self, report: dict) -> None:
        jobs, deployments = report["active"]["jobs"], report["active"]["deployments"]
        if jobs or deployments:
            raise ValueError(f"有 {jobs} 个未结束的构建、{deployments} 个未结束的发布；请等待完成或在页面取消后重试"
                             "（维护期间请暂停提交新任务）")

    def check_sql(self, directory: Path, patch: str, code: Path, image: str) -> List[dict]:
        output = self.task(["apply-sql", "--dir", "/patch/sql", "--patch", patch, "--check", "--json"],
                           code=code, image=image, mounts=[(directory, "/patch/sql")])
        return self._json(output)["scripts"]

    # ------------------------------------------------------------ 备份与恢复
    def backup(self, label: str) -> Path:
        """应用必须已停止：内置数据库用 pg_dump 导出，另打包 config/ 与 data/。外部数据库不在备份范围内。"""
        if self.running(self.app_info()):
            raise RuntimeError("备份前应用必须停止")
        folder = self.root / "backups" / f"backup-{stamp()}"
        folder.mkdir(mode=0o700, parents=True)
        try:
            if self.bundled:
                self.start_database()
                dump = folder / "database.dump"
                self.docker.exec(self.db_name, ["pg_dump", "-U", DB_USER, "-d", DB_NAME, "-Fc"], stdout_path=dump)
                dump.chmod(0o600)
            files = folder / "files.tar.gz"
            with tarfile.open(files, "w:gz") as tar:
                for name in ("config", "data"):
                    for path in (self.root / name).rglob("*"):
                        if path.is_symlink():
                            raise ValueError(f"备份目录含符号链接，拒绝备份：{path}")
                    tar.add(self.root / name, arcname=name)
            files.chmod(0o600)
            save(folder / "snapshot.json", {"schema": 2, "created_at": now(), "label": label,
                                            "database_included": self.bundled, "state": self.state})
            (folder / "SHA256SUMS").write_text(checksums(folder))
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        print(f"备份已保存：{folder}" + ("" if self.bundled else "（外部数据库不在备份范围内，请由 DBA 备份）"),
              flush=True)
        return folder

    def verify_backup(self, folder: Path) -> dict:
        sums = folder / "SHA256SUMS"
        if not sums.is_file():
            raise ValueError(f"{folder} 不是备份目录（缺少 SHA256SUMS）")
        if sums.read_text() != checksums(folder):
            raise ValueError("备份文件缺失、多出或 SHA256 不匹配")
        snapshot = json.loads((folder / "snapshot.json").read_text())
        if snapshot.get("schema") != 2:
            raise ValueError("不是 v2 的备份")
        if self.bundled and not (folder / "database.dump").is_file():
            raise ValueError("备份中没有数据库导出，不能用于内置数据库的恢复")
        return snapshot

    def restore_database(self, dump: Path) -> None:
        self.start_database()
        psql = ["psql", "-U", DB_USER, "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-c"]
        self.docker.exec(self.db_name, [*psql, f"DROP DATABASE IF EXISTS {DB_NAME} WITH (FORCE)"])
        self.docker.exec(self.db_name, [*psql, f"CREATE DATABASE {DB_NAME} OWNER {DB_USER}"])
        self.docker.exec(self.db_name, ["pg_restore", "-U", DB_USER, "-d", DB_NAME, "--no-owner",
                                        "--exit-on-error", "--single-transaction"], stdin_path=dump)

    def restore_files(self, folder: Path) -> None:
        with tempfile.TemporaryDirectory(dir=self.root / "tmp") as temporary:
            unpacked = Path(temporary)
            extract(folder / "files.tar.gz", unpacked)
            for name in ("config", "data"):
                if not (unpacked / name).is_dir():
                    raise ValueError("备份缺少配置或数据目录")
            for name in ("config", "data"):
                if (self.root / name).exists():
                    (self.root / name).rename(unpacked / f"old-{name}")
                (unpacked / name).rename(self.root / name)
                (self.root / name).chmod(0o700)

    def persist(self) -> None:
        target = self.root / ".current-new"
        target.unlink(missing_ok=True)
        target.symlink_to(Path("releases") / version_name(self.state["current"]), target_is_directory=True)
        target.replace(self.root / "current")
        save(self.state_path, self.state)
        temporary = self.root / ".runtime-image-new"
        temporary.write_text(self.state["image"] + "\n")
        temporary.replace(self.root / "runtime-image")

    def recover(self, previous: dict, backup: Path, error: Exception) -> None:
        print(f"更新失败：{error}\n正在恢复到更新前的状态……", file=sys.stderr, flush=True)
        database = self.state["database"]
        try:
            self.remove_app()
            if self.bundled:
                self.restore_database(backup / "database.dump")
            self.restore_files(backup)
            self.state = previous
            self.state["database"] = database  # 数据库容器实际使用的镜像不回退（同一主版本，数据兼容）
            self.persist()
            self.start_app()
        except Exception as again:  # noqa: BLE001 - 两个错误都要告诉运维
            hint = ("外部数据库中已执行的迁移或脚本需要 DBA 用变更前的备份恢复，之后执行 swrctl start"
                    if not self.bundled else f"请检查后执行 swrctl restore {backup}")
            raise RuntimeError(f"{error}；自动恢复也失败：{again}。应用保持停止，{hint}") from None
        if self.bundled:
            print("已恢复更新前的代码、配置、数据文件和数据库，应用已用原版本启动", file=sys.stderr)
        else:
            print("已恢复更新前的代码、配置和数据文件，应用已用原版本启动；外部数据库中已执行的迁移或脚本不会自动撤销，"
                  "请 DBA 核对", file=sys.stderr)

    # ------------------------------------------------------------ 切换版本（补丁、代码包、完整包升级、回滚共用）
    def change(self, version: str, *, action: str, code_dir: Optional[Path] = None,
               sql: Optional[Tuple[Path, str]] = None, patch: Optional[dict] = None,
               db_image: Optional[str] = None, confirmed: bool = False) -> None:
        """切换到代码版本 version 并按需执行 SQL；失败时恢复到切换前的代码、配置、数据文件和（内置）数据库。

        code_dir 是尚未放入 releases/ 的新代码（补丁包解开的 code/），预检通过、停机之后才移入 releases/。
        """
        code = code_dir or self.release_dir(version)
        image = self.runtime_for(self.contract_of(code))
        version_changes = version != self.state["current"] or image != self.state["image"]
        print("[1/5] 预检（应用仍在运行）", flush=True)
        self.start_database()
        current = self.db_status()
        self.ensure_idle(current)
        target = self.db_status(code=code, image=image) if version_changes else current
        if target["unknown"]:
            raise ValueError(f"版本 {version} 不认识数据库中的迁移 {target['unknown']}（数据库来自更新的版本）；"
                             "只切换代码会无法启动，请用 swrctl restore 恢复到当时的备份（swrctl backups 可查看）")
        plan = self.check_sql(sql[0], sql[1], code, image) if sql else []
        pending = [item["path"] for item in plan if item["status"] == "pending"]
        if target["pending"]:
            print(f"  将执行数据库迁移：{target['pending']}")
        if plan:
            print(f"  SQL（{sql[1]}）：{len(pending)} 个将执行，{len(plan) - len(pending)} 个已执行过将跳过")
        changes_database = bool(target["pending"] or pending)
        if not self.bundled and changes_database and not confirmed:
            raise ValueError("使用外部数据库：本次更新会修改数据库，控制器无法替你备份。请 DBA 先备份数据库，"
                             "再加 --db-backup-confirmed 重试")
        if not version_changes and not changes_database and not db_image:
            if patch:
                self.record_patch(patch, sql, [], None)
                save(self.state_path, self.state)
                self.history(action, version, detail="无需停机：代码相同，SQL 均已执行过")
            print("代码与数据库均无需变更")
            return
        print("[2/5] 停止应用", flush=True)
        self.stop_app()
        print("[3/5] 备份", flush=True)
        try:
            backup = self.backup(f"{action} {version} 之前")
        except BaseException:
            # 还没有改动任何东西：用原版本重新启动，再报告备份失败（例如磁盘空间不足）
            self.start_app()
            raise
        previous = copy.deepcopy(self.state)
        try:
            print("[4/5] 切换代码并更新数据库", flush=True)
            if code_dir is not None and not self.release_dir(version).exists():
                code_dir.rename(self.release_dir(version))
            if db_image and db_image != self.state["database"].get("image"):
                self.replace_database(db_image)
            if version != self.state["current"]:
                self.state["previous"] = self.state["current"]
            self.state.update(current=version, image=image, last_backup=str(backup))
            self.task(["upgrade"], stream=True)
            if sql:
                self.task(["apply-sql", "--dir", "/patch/sql", "--patch", sql[1]], mounts=[(sql[0], "/patch/sql")],
                          stream=True)
            print("[5/5] 启动并等待健康检查", flush=True)
            self.start_app()
            if patch:
                self.record_patch(patch, sql, pending, backup)
            self.persist()
        except BaseException as error:  # 包括 Ctrl+C：已停机、可能已改数据库，必须恢复
            self.history(action, version, "failed", error=str(error) or type(error).__name__, backup=str(backup))
            self.recover(previous, backup, error)
            raise
        self.history(action, version, backup=str(backup), migrations=target["pending"], sql=pending)
        print(f"已切换到 {version}，健康检查通过；更新前的备份：{backup}")

    def record_patch(self, patch: dict, sql: Optional[Tuple[Path, str]], applied: List[str],
                     backup: Optional[Path]) -> None:
        folder = self.root / "patches" / patch["version"]
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        source = patch["_directory"]
        for name in (PATCH_MANIFEST, "README.md"):
            if (source / name).is_file():
                shutil.copy2(source / name, folder / name)
        if sql and sql[0].is_dir():
            shutil.copytree(sql[0], folder / "sql")
        self.state.setdefault("patches", []).append({
            "version": patch["version"], "applied_at": now(), "code": bool(patch.get("code")),
            "sql": applied, "base": patch.get("base", ""), "description": patch.get("description", ""),
            "backup": str(backup) if backup else None})

    # ------------------------------------------------------------ 命令
    def apply(self, archive: Path, confirmed: bool = False) -> None:
        """应用补丁包（swr-patch-*.tar.gz），也接受 v1 方式的代码包（swr-code-*.tar.gz）。"""
        archive = archive.resolve()
        if not archive.is_relative_to(self.root):
            raise ValueError(f"请先把补丁包放到部署目录内（例如 {self.root / 'updates'}）")
        checksum = archive.with_name(archive.name + ".sha256")
        if checksum.is_file() and checksum.read_text().split()[0] != digest(archive):
            raise ValueError(f"{archive.name} 与 {checksum.name} 不一致，文件可能损坏")
        with tempfile.TemporaryDirectory(prefix="apply-", dir=self.root / "tmp") as temporary:
            package = Path(temporary) / "package"
            package.mkdir()
            extract(archive, package)
            if (package / PATCH_MANIFEST).is_file():
                manifest: Optional[dict] = verify_patch(package)
                version = manifest["version"]
                if any(item["version"] == version for item in self.state.get("patches", [])):
                    raise ValueError(f"补丁 {version} 已经应用过；需要切回它的代码请用 swrctl rollback {version}")
                if manifest.get("base") and manifest["base"] != self.state["current"]:
                    raise ValueError(f"补丁 {version} 只能用于版本 {manifest['base']}，当前是 {self.state['current']}")
                code_dir = package / "code" if manifest.get("code") else None
                sql = (package / "sql", version) if manifest.get("sql") else None
                manifest["_directory"] = package
            elif (package / CODE_MANIFEST).is_file():
                code = json.loads((package / CODE_MANIFEST).read_text())
                verify(package, code.get("runtime_contract", ""))
                version, code_dir, sql, manifest = code["version"], package, None, None
                if version == self.state["current"]:
                    print("已经是此版本，无需更新")
                    return
            else:
                raise ValueError("不是补丁包或代码包（缺少 patch.json / release.json）")
            # 有代码：切换到补丁（代码包）的版本；只有 SQL：代码保持当前版本
            target = version if code_dir is not None else self.state["current"]
            if code_dir is not None:
                contract = self.contract_of(code_dir)
                self.runtime_for(contract)
                existing = self.release_dir(version)
                if existing.exists():
                    if verify(existing, contract)["files"] != verify(code_dir, contract)["files"]:
                        raise ValueError(f"releases/{version} 已存在且内容不同；补丁必须使用新的版本号")
                    code_dir = None  # 同样的代码已在 releases/ 中（例如回滚后再次前进）
            self.change(target, action="apply", code_dir=code_dir, sql=sql, patch=manifest, confirmed=confirmed)

    def rollback(self, version: Optional[str]) -> None:
        version = version or self.state.get("previous")
        if not version:
            raise ValueError("没有上一版本，请显式指定 releases 中的版本")
        if version == self.state["current"]:
            print("已经是此版本，无需回滚")
            return
        self.verify_release(version)
        self.change(version, action="rollback")

    def restore(self, folder: Path, skip_safety_backup: bool = False) -> None:
        folder = folder.resolve()
        snapshot = self.verify_backup(folder)
        target = snapshot["state"]
        self.verify_release(target["current"])
        image = self.runtime_for(self.contract_of(self.release_dir(target["current"])))
        if not self.bundled:
            print("外部数据库不在备份中：请 DBA 把数据库恢复到同一时间点，否则代码、文件与数据库可能不一致", file=sys.stderr)
        running = self.running(self.app_info())
        self.stop_app()
        try:
            safety = None if skip_safety_backup else self.backup("restore 之前的安全备份")
        except BaseException:
            if running:
                self.start_app()
            raise
        previous = copy.deepcopy(self.state)
        try:
            self.remove_app()
            if self.bundled:
                self.restore_database(folder / "database.dump")
            self.restore_files(folder)
            self.state.update(current=target["current"], previous=target.get("previous"), image=image,
                              patches=target.get("patches", []))
            self.persist()
            self.start_app()
        except BaseException as error:
            self.history("restore", target["current"], "failed", error=str(error) or type(error).__name__,
                         backup=str(folder))
            if safety is None:
                raise
            self.recover(previous, safety, error)
            raise
        self.history("restore", target["current"], backup=str(folder), safety_backup=str(safety) if safety else None)
        print(f"已恢复到备份 {folder.name}（版本 {target['current']}）；备份之后的数据已被替换"
              + (f"，恢复前的安全备份：{safety}" if safety else ""))

    def backups(self) -> List[dict]:
        rows = []
        for folder in sorted((self.root / "backups").glob("backup-*")):
            try:
                snapshot = json.loads((folder / "snapshot.json").read_text())
            except (OSError, ValueError):
                continue
            rows.append({"backup": str(folder), "created_at": snapshot.get("created_at"), "label": snapshot.get("label"),
                         "version": snapshot.get("state", {}).get("current"),
                         "patches": [p["version"] for p in snapshot.get("state", {}).get("patches", [])],
                         "database_included": snapshot.get("database_included")})
        return rows

    def status(self) -> dict:
        info = self.app_info()
        report = {"version": self.state["current"], "previous": self.state.get("previous"),
                  "patches": [p["version"] for p in self.state.get("patches", [])],
                  "image": self.state["image"], "port": self.state["port"],
                  "container_state": info["State"]["Status"] if info else "absent",
                  "health": info["State"].get("Health", {}).get("Status") if info else None,
                  "database": {"mode": self.state["database"]["mode"]}}
        if self.bundled:
            db = self.db_info()
            report["database"].update(container=self.db_name, container_state=db["State"]["Status"] if db else "absent",
                                      health=db["State"].get("Health", {}).get("Status") if db else None)
        return report

    def verify_all(self) -> None:
        self.verify_release(self.state["current"])
        subprocess.run(["python", "/opt/swr-runtime/check_dependencies.py"], check=True)
        if self.bundled:
            self.wait_healthy(self.db_name, 60, "数据库")
        report = self.db_status()
        if report["pending"] or report["unknown"]:
            raise RuntimeError(f"数据库迁移与代码不一致：待执行 {report['pending']}，代码不认识 {report['unknown']}")
        self.wait_healthy(self.app_name, 60, "应用")
        print(f"代码完整性、ARM64 依赖、数据库（迁移 {report['migrations']}）及应用健康检查均通过")

    # ------------------------------------------------------------ 安装
    def install(self, args: argparse.Namespace) -> None:
        if self.state:
            raise ValueError("此目录已部署：补丁用 swrctl apply，更换运行镜像用新完整包的 upgrade.sh；不要覆盖安装")
        for name in ("config", "data", "pgdata"):
            path = self.root / name
            if path.exists() and any(path.iterdir()):
                raise ValueError(f"{path} 已有内容，拒绝覆盖；从 v1 迁移请安装到新目录并用 SWR_IMPORT_V1（见部署文档）")
        if not 1 <= args.port <= 65535:
            raise ValueError("端口范围为 1–65535")
        bundled = args.database == "bundled"
        for value in (args.name, args.network, *([args.db_name] if bundled else [])):
            if not NAME_RE.fullmatch(value):
                raise ValueError(f"名称 {value!r} 只能包含字母、数字、点、下划线和短横线")
        if bundled and args.network in RESERVED_NETWORKS:
            raise ValueError("内置数据库需要自定义 bridge 网络（默认 swr），不能使用 bridge / host / none")
        if bundled and not args.postgres_image:
            raise ValueError("缺少 PostgreSQL 镜像（完整包的 images/postgres-arm64.tar.gz）")
        operator_env = read_env(args.env_file) if args.env_file else []
        values = env_values(operator_env)
        if bundled and any(key in values for key in DATABASE_ENV):
            raise ValueError("使用内置数据库时不要在环境文件中设置 BUILDER_DATABASE_*；使用外部数据库请设置 SWR_DATABASE=external")
        if not bundled and not values.get("BUILDER_DATABASE_URL"):
            raise ValueError("使用外部数据库时，SWR_ENV_FILE 指定的环境文件必须包含 BUILDER_DATABASE_URL"
                             "（以及 BUILDER_DATABASE_PASSWORD）")
        v1 = self.v1_source(Path(args.import_v1)) if args.import_v1 else None
        for name in (args.name, *([args.db_name] if bundled else [])):
            info = self.docker.container(name)
            if info and v1 and info.get("Config", {}).get("Labels", {}).get("swr.deployment-root") == str(v1["root"]):
                # v1 已停止的同名容器（默认都叫 swr-builder）：v1 的 swrctl start 会按 deploy.json 重新创建，可以删除
                self.docker.remove(name)
                print(f"已删除 v1 已停止的容器 {name}（需要切回 v1 时，{v1['root']}/swrctl start 会重新创建）")
            elif info:
                raise ValueError(f"容器 {name} 已存在，拒绝重复安装")
        major = ""
        if bundled:
            info = self.docker.image(args.postgres_image)
            if not info:
                raise ValueError("PostgreSQL 镜像未导入")
            major = pg_major(info)
        self.state = {"schema": 2, "container": args.name, "bind": args.bind, "port": args.port,
                      "network": args.network, "current": "", "previous": None, "image": args.image,
                      "runtimes": {self.contract: args.image}, "patches": [], "installed_at": now(),
                      "database": ({"mode": "bundled", "container": args.db_name, "image": args.postgres_image,
                                    "major": major} if bundled else {"mode": "external"})}
        created: List[Path] = []
        try:
            self._install_files(args.bundle, operator_env, v1, created)
            print("[1/4] 启动数据库并执行迁移", flush=True)
            if bundled:
                self.ensure_network()
                self.run_database(args.postgres_image)
                self.wait_healthy(self.db_name, 180, "数据库")
            else:
                self.ensure_network()
            report = self.db_status()
            if report["users"]:
                raise ValueError("数据库中已有平台账号；全新安装需要空的数据库")
            self.task(["upgrade"], stream=True)
            if v1:
                print("[2/4] 导入 v1 的 SQLite 数据", flush=True)
                self.import_v1(v1)
            seed = args.bundle / "seed/sql"
            if seed.is_dir() and any(seed.rglob("*.sql")):
                print("[3/4] 执行铺底 SQL", flush=True)
                folder = Path(tempfile.mkdtemp(prefix="seed-", dir=self.root / "tmp"))
                shutil.copytree(seed, folder / "sql")
                try:
                    self.task(["apply-sql", "--dir", "/patch/sql", "--patch", SEED_PATCH],
                              mounts=[(folder / "sql", "/patch/sql")], stream=True)
                finally:
                    shutil.rmtree(folder, ignore_errors=True)
            print("[4/4] 启动应用", flush=True)
            self.start_app()
            self.persist()
        except BaseException:
            self.abort_install(created)
            raise
        self.history("install", self.state["current"], database=self.state["database"]["mode"],
                     imported_v1=str(v1["root"]) if v1 else None)
        print(f"安装成功：http://<服务器IP>:{args.port}；管理入口：{self.root}/swrctl")
        if v1:
            print("已导入 v1 的数据：账号与密码沿用 v1；确认无误前请保留 v1 目录，不要同时启动两套平台")
        else:
            print(f"首次管理员 admin 的初始密码：{self.root}/data/admin-initial-password.txt（请在服务器本地读取）")

    def _install_files(self, bundle: Path, operator_env: List[str], v1: Optional[dict], created: List[Path]) -> None:
        for name in DIRECTORIES + (("pgdata",) if self.bundled else ()):
            path = self.root / name
            if not path.exists():
                path.mkdir(mode=0o700)
                created.append(path)
        for name in ("swrctl", "部署文档.md", "deploy.json", "current", "runtime-image"):
            if not (self.root / name).exists():
                created.append(self.root / name)
        for path in (bundle / "ops").iterdir():
            if path.is_file():
                shutil.copy2(path, self.root / "ops" / path.name)
        shutil.copy2(bundle / "swrctl", self.root / "swrctl")
        (self.root / "swrctl").chmod(0o755)
        shutil.copy2(bundle / "部署文档.md", self.root / "部署文档.md")
        config = self.root / "config"
        (config / "certs").mkdir(exist_ok=True)
        shutil.copy2(v1["config"] / "config.yaml" if v1 and v1.get("config_file") else bundle / "config.yaml",
                     config / "config.yaml")
        lines = list(operator_env) or read_env(bundle / "swr-builder.env.example")
        if v1 and v1.get("env"):
            # v1 的环境变量（仓库凭证、BUILDER_SECRET_KEY 等）在前，本次指定的同名变量覆盖它们
            overridden = set(env_values(operator_env)) | set(DATABASE_ENV)
            inherited = [line for line in v1["env"] if line.strip().partition("=")[0] not in overridden]
            lines = [f"# ---- 从 v1 部署 {v1['root']} 导入 ----", *inherited, *operator_env]
        if self.bundled:
            password = secrets.token_urlsafe(24)
            lines += ["", MANAGED_BEGIN,
                      f"BUILDER_DATABASE_URL=postgresql://{DB_USER}@{self.db_name}:5432/{DB_NAME}",
                      f"BUILDER_DATABASE_PASSWORD={password}"]
            write_private(config / "postgres.env", f"POSTGRES_USER={DB_USER}\nPOSTGRES_DB={DB_NAME}\n"
                                                   f"POSTGRES_PASSWORD={password}\n")
        write_private(config / "swr-builder.env", "\n".join(lines).rstrip("\n") + "\n")
        self.copy_certs(bundle / "seed/certs")
        if v1 and v1.get("config"):
            self.copy_certs(v1["config"] / "certs")
        self.load_seed_images(bundle / "seed/images")
        archives = list((bundle / "releases").glob("*.tar.gz"))
        if len(archives) != 1:
            raise ValueError("完整包必须包含且仅包含一个代码发布包")
        self.state["current"] = self.stage(archives[0])
        extract(bundle / "source.tar.gz", self.root / "source")
        if v1:
            shutil.copytree(v1["data"], self.root / "data", dirs_exist_ok=True,
                            ignore=lambda folder, names: [n for n in names if n in SQLITE_FILES]
                            if Path(folder) == v1["data"] else [])
        save(self.state_path, self.state)
        self.persist()

    def abort_install(self, created: List[Path]) -> None:
        print("安装失败，正在清理……", file=sys.stderr, flush=True)
        for name in (self.state.get("container"), self.state.get("database", {}).get("container")):
            if name:
                try:
                    info = self.docker.container(name)
                    if info and info.get("Config", {}).get("Labels", {}).get("swr.deployment-root") == str(self.root):
                        self.docker.remove(name)
                except Exception as exc:  # noqa: BLE001 - 清理尽力而为，原始错误更重要
                    print(f"  删除容器 {name} 失败：{exc}", file=sys.stderr)
        leftovers = [path for path in created if path.exists() or path.is_symlink()]
        if leftovers:
            target = self.root / f"failed-install-{stamp()}"
            target.mkdir(mode=0o700)
            for path in leftovers:
                path.rename(target / path.name)
            print(f"  本次安装生成的文件（含数据库数据目录）已移到 {target}，确认不需要后可删除；"
                  "修复问题后重新执行 install.sh", file=sys.stderr)
        self.state = {}

    def stage(self, archive: Path) -> str:
        """解开完整包里的代码包，校验后放入 releases/；返回版本号。"""
        with tempfile.TemporaryDirectory(prefix="stage-", dir=self.root / "tmp") as temporary:
            staged = Path(temporary) / "release"
            staged.mkdir()
            extract(archive, staged)
            manifest = verify(staged, self.contract)
            version = manifest["version"]
            destination = self.release_dir(version)
            if destination.exists():
                if verify(destination, self.contract)["files"] != manifest["files"]:
                    raise ValueError("已有同版本但内容不同；必须使用新的版本名")
            else:
                staged.rename(destination)
            return version

    def copy_certs(self, source: Path) -> None:
        if not source.is_dir():
            return
        target = self.root / "config/certs"
        for path in sorted(source.iterdir()):
            if not path.is_file() or path.is_symlink():
                continue
            existing = target / path.name
            if existing.exists():
                if digest(existing) != digest(path):
                    print(f"  证书 {path.name} 已存在且内容不同，保留现有文件", file=sys.stderr)
                continue
            shutil.copy2(path, existing)
            existing.chmod(0o600)

    def load_seed_images(self, source: Path) -> None:
        if not source.is_dir():
            return
        for path in sorted(source.iterdir()):
            if path.is_file() and path.name.endswith((".tar", ".tar.gz")):
                print(f"  导入业务基础镜像 {path.name}", flush=True)
                print("  " + self.docker.load(path).replace("\n", "\n  "))

    # ------------------------------------------------------------ v1 迁移
    def v1_source(self, path: Path) -> dict:
        path = path.resolve()
        if path == self.root or path.is_relative_to(self.root) or self.root.is_relative_to(path):
            raise ValueError("v2 必须安装到与 v1 不重叠的新目录")
        source: dict = {"root": path, "config": None, "env": None, "config_file": False}
        if (path / "data/builder.db").is_file():
            source["data"] = path / "data"
            if (path / "config").is_dir():
                source["config"] = path / "config"
        elif (path / "builder.db").is_file():
            source["data"] = path
        else:
            raise ValueError(f"{path} 中没有 v1 数据库（data/builder.db 或 builder.db）")
        state_file = path / "deploy.json"
        if state_file.is_file():
            name = json.loads(state_file.read_text()).get("container")
            info = self.docker.container(name) if name else None
            if self.running(info):
                raise ValueError(f"v1 平台（容器 {name}）仍在运行，请先执行 {path}/swrctl stop")
        if source["config"]:
            source["config_file"] = (source["config"] / "config.yaml").is_file()
            env = source["config"] / "swr-builder.env"
            if env.is_file():
                source["env"] = read_env(env)
        if not (source["data"] / "secret.key").is_file() and not env_values(source["env"] or []).get("BUILDER_SECRET_KEY"):
            raise ValueError("v1 数据目录中没有 secret.key，环境文件中也没有 BUILDER_SECRET_KEY；缺少主密钥时已保存的凭证无法解密")
        return source

    def import_v1(self, v1: dict) -> None:
        folder = Path(tempfile.mkdtemp(prefix="import-", dir=self.root / "tmp"))
        try:
            for name in SQLITE_FILES:
                if (v1["data"] / name).is_file():
                    shutil.copy2(v1["data"] / name, folder / name)
            # 可写挂载：SQLite 读取 WAL 模式的库时可能需要创建 -shm 文件（这里是副本，不影响 v1 原文件）
            self.task(["import-sqlite", "--from", "/import/builder.db"], mounts=[(folder, "/import")],
                      writable=True, stream=True)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    # ------------------------------------------------------------ 完整包升级
    def upgrade(self, args: argparse.Namespace) -> None:
        bundle = args.bundle
        db_image = None
        if self.bundled and args.postgres_image and args.postgres_image != self.state["database"].get("image"):
            info = self.docker.image(args.postgres_image)
            if not info:
                raise ValueError("PostgreSQL 镜像未导入")
            if pg_major(info) != self.state["database"].get("major"):
                raise ValueError(f"完整包的 PostgreSQL 主版本 {pg_major(info)} 与当前 {self.state['database'].get('major')} "
                                 "不同，不能直接替换数据目录；请联系开发提供主版本迁移方案")
            db_image = args.postgres_image
        self.state.setdefault("runtimes", {})[self.contract] = args.image
        archives = list((bundle / "releases").glob("*.tar.gz"))
        if len(archives) != 1:
            raise ValueError("完整包必须包含且仅包含一个代码发布包")
        work = Path(tempfile.mkdtemp(prefix="upgrade-", dir=self.root / "tmp"))
        try:
            code = work / "code"
            code.mkdir()
            extract(archives[0], code)
            version = verify(code, self.contract)["version"]
            existing = self.release_dir(version)
            if existing.exists():
                if verify(existing, self.contract)["files"] != verify(code, self.contract)["files"]:
                    raise ValueError("releases/ 中已有同版本但内容不同；完整包必须使用新的版本名")
                code_dir = None
            else:
                code_dir = code
            source = self.root / "source-versions" / version
            seed = bundle / "seed/sql"
            sql = None
            if seed.is_dir() and any(seed.rglob("*.sql")):
                shutil.copytree(seed, work / "sql")
                sql = (work / "sql", SEED_PATCH)
            save(self.state_path, self.state)  # 先登记新运行镜像，失败后也能再次执行 upgrade.sh
            self.change(version, action="upgrade", code_dir=code_dir, sql=sql, db_image=db_image,
                        confirmed=args.db_backup_confirmed)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        if not source.exists():
            source.mkdir(parents=True)
            extract(bundle / "source.tar.gz", source)
        for path in (bundle / "ops").iterdir():
            if path.is_file():
                shutil.copy2(path, self.root / "ops" / path.name)
        shutil.copy2(bundle / "swrctl", self.root / "swrctl")
        (self.root / "swrctl").chmod(0o755)
        shutil.copy2(bundle / "部署文档.md", self.root / "部署文档.md")
        self.copy_certs(bundle / "seed/certs")
        self.load_seed_images(bundle / "seed/images")
        print(f"运行镜像已更新，配置和数据保留；新版源码：{source}")

    # ------------------------------------------------------------ 制作补丁
    def build_patch(self, args: argparse.Namespace) -> Path:
        source = (args.source or self.root / "source").resolve()
        for path in (source, args.sql, args.readme):
            if path is not None and not Path(path).resolve().is_relative_to(self.root):
                raise ValueError(f"{path} 不在部署目录内；管理容器只能看到 {self.root}")
        sql = Path(args.sql).resolve() if args.sql else None
        readme = Path(args.readme).resolve() if args.readme else None
        output = self.root / "updates"
        if args.no_code:
            return create_patch(source, output, args.version, self.contract, code=False, sql=sql, base=args.base,
                                description=args.description, readme=readme)
        if digest(source / "web/package-lock.json") != digest(Path("/opt/swr-web-deps/package-lock.json")):
            raise ValueError("前端依赖已变化，请用联网构建机生成新版完整包")
        if digest(source / "deploy/offline/requirements.lock") != digest(Path("/opt/swr-runtime/requirements.lock")):
            raise ValueError("Python 依赖锁已变化，请用联网构建机生成新版完整包")
        if digest(source / "deploy/offline/runtime-lock.json") != self.contract:
            raise ValueError("运行环境标识已变化，请使用匹配的完整包")
        with tempfile.TemporaryDirectory(prefix="swr-patch-build-", dir=self.root / "tmp") as temporary:
            workspace = Path(temporary)
            shutil.copytree(source / "swr_builder", workspace / "swr_builder",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "webui"))
            shutil.copytree(source / "web", workspace / "web",
                            ignore=shutil.ignore_patterns("node_modules", "dist", ".env", ".env.*"))
            (workspace / "web/node_modules").symlink_to("/opt/swr-web-deps/node_modules")
            # npm run build 只运行本地编译工具；无需 npm install，不会访问 npm 仓库。
            subprocess.run(["npm", "--prefix", str(workspace / "web"), "run", "build"], check=True)
            return create_patch(workspace, output, args.version, self.contract, sql=sql, base=args.base,
                                description=args.description, readme=readme)


def pg_major(image: dict) -> str:
    for item in image.get("Config", {}).get("Env") or []:
        if item.startswith("PG_MAJOR="):
            return item.split("=", 1)[1]
    raise ValueError("镜像中没有 PG_MAJOR，不是官方 PostgreSQL 镜像")


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SWR 构建平台离线部署管理（由 install.sh / upgrade.sh / swrctl 调用）")
    parser.add_argument("--root", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    install = sub.add_parser("install")
    install.add_argument("--bundle", type=Path, required=True)
    install.add_argument("--image", required=True)
    install.add_argument("--postgres-image", default="")
    install.add_argument("--database", choices=("bundled", "external"), default="bundled")
    install.add_argument("--port", type=int, default=8082)
    install.add_argument("--name", default="swr-builder")
    install.add_argument("--db-name", default="swr-postgres")
    install.add_argument("--bind", default="0.0.0.0")
    install.add_argument("--network", default="")
    install.add_argument("--env-file", type=Path)
    install.add_argument("--import-v1")
    upgrade = sub.add_parser("upgrade")
    upgrade.add_argument("--bundle", type=Path, required=True)
    upgrade.add_argument("--image", required=True)
    upgrade.add_argument("--postgres-image", default="")
    upgrade.add_argument("--db-backup-confirmed", action="store_true")
    for command in ("status", "start", "restart", "backup", "verify", "backups", "history"):
        sub.add_parser(command)
    stop = sub.add_parser("stop")
    stop.add_argument("--with-db", action="store_true", help="同时停止内置数据库")
    logs = sub.add_parser("logs")
    logs.add_argument("--tail", type=int, default=100)
    logs.add_argument("--db", action="store_true", help="查看内置数据库的日志")
    for command in ("apply", "update"):
        apply = sub.add_parser(command)
        apply.add_argument("archive", type=Path)
        apply.add_argument("--db-backup-confirmed", action="store_true",
                           help="外部数据库：确认 DBA 已备份数据库")
    rollback = sub.add_parser("rollback")
    rollback.add_argument("version", nargs="?")
    restore = sub.add_parser("restore")
    restore.add_argument("backup", type=Path)
    restore.add_argument("--skip-safety-backup", action="store_true",
                         help="不先备份当前状态（当前数据库已损坏、无法导出时使用）")
    build = sub.add_parser("build-patch")
    build.add_argument("version")
    build.add_argument("--source", type=Path)
    build.add_argument("--sql", type=Path, help="SQL 目录（含 ddl/、dml/），必须在部署目录内")
    build.add_argument("--no-code", action="store_true", help="只含 SQL，不打入代码")
    build.add_argument("--base", default="", help="只允许应用到这个版本")
    build.add_argument("--description", default="")
    build.add_argument("--readme", type=Path)
    legacy = sub.add_parser("build-update", help="兼容 v1：等同于 build-patch <版本> --source <源码>")
    legacy.add_argument("source", type=Path)
    legacy.add_argument("version")
    return parser


def run(argv: Optional[List[str]] = None, *, docker: Optional[Docker] = None, contract: Optional[str] = None,
        sleep: Callable[[float], None] = time.sleep) -> None:
    args = parser().parse_args(argv)
    args.root.mkdir(parents=True, exist_ok=True)
    with (args.root / ".swrctl.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("已有安装、更新或管理命令正在执行") from None
        deployment = Deployment(args.root, docker=docker, contract=contract, sleep=sleep)
        if args.command == "install":
            if not args.network:
                args.network = "swr" if args.database == "bundled" else "bridge"
            deployment.install(args)
            return
        if not deployment.state:
            raise ValueError("此目录尚未安装")
        command = args.command
        if command == "upgrade":
            deployment.upgrade(args)
        elif command == "status":
            print(json.dumps(deployment.status(), indent=2, ensure_ascii=False))
        elif command == "logs":
            deployment.docker.logs(deployment.db_name if args.db and deployment.bundled else deployment.app_name,
                                   args.tail)
        elif command == "verify":
            deployment.verify_all()
        elif command == "start":
            if deployment.running(deployment.app_info()):
                deployment.start_database()
                print("应用已在运行")
            else:
                deployment.start_app()
                print("已启动，健康检查通过")
        elif command == "restart":
            deployment.start_database()
            if deployment.running(deployment.app_info()):
                deployment.ensure_idle(deployment.db_status())
            deployment.stop_app()
            deployment.start_app()
            print("已重启，健康检查通过")
        elif command == "stop":
            if deployment.running(deployment.app_info()):
                try:
                    deployment.ensure_idle(deployment.db_status())
                except RuntimeError as exc:  # 数据库不可用时应用也处理不了任务，允许停止
                    print(f"无法确认任务状态（{exc}），继续停止", file=sys.stderr)
            deployment.stop_app()
            if args.with_db:
                deployment.stop_database()
            print("已停止" + ("（含数据库）" if args.with_db and deployment.bundled else ""))
        elif command == "backup":
            running = deployment.running(deployment.app_info())
            if running:
                deployment.start_database()
                deployment.ensure_idle(deployment.db_status())
            deployment.stop_app()
            try:
                deployment.backup("手动备份")
            finally:
                if running:
                    deployment.start_app()
        elif command == "backups":
            print(json.dumps(deployment.backups(), indent=2, ensure_ascii=False))
        elif command == "history":
            path = deployment.root / "history.log"
            print(path.read_text(encoding="utf-8") if path.exists() else "", end="")
        elif command in ("apply", "update"):
            deployment.apply(args.archive, confirmed=args.db_backup_confirmed)
        elif command == "rollback":
            deployment.rollback(args.version)
        elif command == "restore":
            deployment.restore(args.backup, skip_safety_backup=args.skip_safety_backup)
        elif command in ("build-patch", "build-update"):
            if command == "build-update":
                args = argparse.Namespace(version=args.version, source=args.source, sql=None, no_code=False,
                                          base="", description="", readme=None)
            print(f"补丁包：{deployment.build_patch(args)}")


class Terminated(BaseException):
    """收到 SIGTERM：按 Ctrl+C 处理，正在进行的更新会自动恢复。"""


def _terminate(_signum, _frame):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)  # 恢复过程中不再被打断
    raise Terminated("收到终止信号")


def main() -> None:
    import os
    os.umask(0o077)
    # SSH 断开（SIGHUP）不中断正在执行的操作：重新登录后用 swrctl status / history 查看结果
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, _terminate)
    try:
        run()
    except (OSError, ValueError, RuntimeError, KeyError, tarfile.TarError, subprocess.CalledProcessError) as exc:
        print(f"操作失败：{exc}", file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, Terminated):
        print("操作已中止；用 swrctl status 与 swrctl history 查看当前状态", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
