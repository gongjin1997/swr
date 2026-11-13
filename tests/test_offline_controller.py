"""离线部署控制器（deploy/offline/manage.py）：用假的 Docker 验证安装、补丁、升级、回滚、恢复的编排与失败恢复。

假 Docker 按真实命令行解析一次性容器的参数（swr_builder.__main__.db_parser），挂载的宿主机路径必须存在，
SQL 目录用真实的 sqlscripts.discover 校验；数据库本身用内存模型模拟：pg_dump 导出模型、pg_restore 还原模型。
db status / apply-sql 在真实数据库上的行为见 tests/test_sql_scripts.py；真实 Docker 与 ARM64 主机上的验收见
scripts/verify-arm64-deployment.py。
"""
import copy
import json
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "deploy/offline"))
import manage  # noqa: E402
from release import create, create_patch, extract  # noqa: E402

from swr_builder.__main__ import db_parser  # noqa: E402
from swr_builder.store import sqlscripts  # noqa: E402

CONTRACT = "c" * 64
APP_IMAGE = "sha256:" + "a" * 64
PG_IMAGE = "sha256:" + "b" * 64


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def empty_db() -> dict:
    return {"migrations": [], "scripts": {}, "active": {"jobs": 0, "deployments": 0}, "users": 0, "rows": []}


def option_values(options, flag):
    return [options[i + 1] for i, value in enumerate(options) if value == flag]


def mounts_of(options) -> dict:
    """--mount type=bind,src=X,dst=Y[,readonly] → {Y: X}"""
    result = {}
    for value in option_values(options, "--mount"):
        fields = dict(item.split("=", 1) for item in value.split(",") if "=" in item)
        result[fields["dst"]] = fields["src"]
    return result


def code_info(code: str) -> dict:
    return json.loads((Path(code) / "swr_builder/qa.json").read_text())


class FakeDocker:
    def __init__(self):
        self.containers = {}
        self.networks = {}
        self.images = {APP_IMAGE: {"Id": APP_IMAGE, "Architecture": "arm64", "Config": {"Env": []}},
                       PG_IMAGE: {"Id": PG_IMAGE, "Architecture": "arm64", "Config": {"Env": ["PG_MAJOR=16"]}}}
        self.db = empty_db()
        self.calls = []
        self.loaded = []
        self.app_fails = False
        self.dump_fails = False

    def add_image(self, image, major=None):
        self.images[image] = {"Id": image, "Architecture": "arm64",
                              "Config": {"Env": [f"PG_MAJOR={major}"] if major else []}}

    # ---- 查询
    def info(self):
        return "28.5.2"

    def container(self, name):
        item = self.containers.get(name)
        if not item:
            return None
        return {"State": {"Running": item["running"], "Status": "running" if item["running"] else "exited",
                          "Health": {"Status": item["health"]}},
                "Config": {"Labels": item["labels"], "Image": item["image"]}}

    def image(self, ref):
        return self.images.get(ref)

    def network(self, name):
        return self.networks.get(name)

    def create_network(self, name, labels):
        self.networks[name] = {"Driver": "bridge", "Labels": labels}

    # ---- 容器
    def _check_paths(self, options):
        mounts = mounts_of(options)
        for target, source in mounts.items():
            if source != "/var/run/docker.sock":
                assert Path(source).exists(), f"挂载 {target} 的宿主机路径不存在：{source}"
        for env_file in option_values(options, "--env-file"):
            assert Path(env_file).is_file(), env_file
        return mounts

    def run_service(self, name, image, options, command=()):
        assert name not in self.containers, f"容器 {name} 已存在"
        assert image in self.images, image
        mounts = self._check_paths(options)
        labels = dict(value.split("=", 1) for value in option_values(options, "--label"))
        item = {"image": image, "options": list(options), "command": list(command), "labels": labels,
                "mounts": mounts, "running": True, "health": "healthy"}
        self.containers[name] = item
        self.calls.append(("run", name))
        if labels["swr.role"] == "database":
            data = Path(mounts[manage.PG_DATA])
            if not any(data.iterdir()):  # 空数据目录：initdb 出一个空库
                self.db = empty_db()
                (data / "PG_VERSION").write_text("16\n")
        else:
            self._boot(item)

    def _boot(self, item):
        """应用启动：执行自己认识的迁移、首次启动创建 admin；数据库比代码新或代码损坏时健康检查失败。"""
        code = code_info(item["mounts"][manage.APP_CODE])
        unknown = [v for v in self.db["migrations"] if v not in code["migrations"]]
        if code["broken"] or unknown or self.app_fails:
            item["health"] = "unhealthy"
            return
        self.db["migrations"] = sorted(set(self.db["migrations"]) | set(code["migrations"]))
        self.db["users"] = self.db["users"] or 1

    def start(self, name):
        item = self.containers[name]
        item["running"], item["health"] = True, "healthy"
        if item["labels"]["swr.role"] == "app":
            self._boot(item)

    def stop(self, name, timeout):
        self.containers[name]["running"] = False
        self.calls.append(("stop", name))

    def remove(self, name):
        self.containers.pop(name)
        self.calls.append(("rm", name))

    def load(self, path):
        self.loaded.append(Path(path).name)
        return f"Loaded image: {Path(path).name}"

    def logs(self, name, tail):
        print(f"logs of {name}")

    def exec(self, name, command, stdin_path=None, stdout_path=None):
        assert self.containers[name]["running"], f"{name} 未运行"
        self.calls.append(("exec", command[0]))
        if command[0] == "pg_dump":
            if self.dump_fails:
                stdout_path.write_text("partial")
                raise RuntimeError("pg_dump 失败：No space left on device")
            stdout_path.write_text(json.dumps(self.db))
        elif command[0] == "psql":
            if command[-1].startswith("DROP DATABASE"):
                self.db = empty_db()
        elif command[0] == "pg_restore":
            self.db = json.loads(stdin_path.read_text())
        else:
            raise AssertionError(command)
        return ""

    def run_task(self, image, options, command, stream=False):
        assert command[:4] == ["python", "-m", "swr_builder", "db"], command
        args = db_parser().parse_args(command[4:])  # 真实命令行能接受的参数
        assert image in self.images
        mounts = self._check_paths(options)
        code = code_info(mounts[manage.APP_CODE])
        self.calls.append(("task", args.action))
        status = {"version": code["version"], "migrations": self.db["migrations"],
                  "pending": [v for v in code["migrations"] if v not in self.db["migrations"]],
                  "unknown": [v for v in self.db["migrations"] if v not in code["migrations"]],
                  "active": self.db["active"], "scripts": len(self.db["scripts"]), "users": self.db["users"],
                  "flavor": "postgresql"}
        if args.action == "status":
            return "启动日志会写到 stderr，不在这里\n" + json.dumps(status) + "\n"
        if args.action == "upgrade":
            if status["unknown"]:
                raise RuntimeError("db upgrade 失败：数据库已由更新版本的程序迁移")
            self.db["migrations"] = sorted(set(self.db["migrations"]) | set(code["migrations"]))
            write(Path(mounts[manage.APP_DATA]) / f"migrated-by-{code['version']}.txt", "upgrade 写入的数据文件")
            return ""
        if args.action == "apply-sql":
            scripts = sqlscripts.discover(Path(mounts[args.dir]))
            plan = []
            for script in scripts:
                recorded = self.db["scripts"].get(f"{args.patch}/{script.path}")
                if recorded and recorded != script.sha256:
                    raise RuntimeError(f"apply-sql 失败：{script.path} 已在 {args.patch} 中执行过，但内容与本次不同")
                plan.append({"path": script.path, "status": "skipped" if recorded else "pending"})
            if args.check:
                return json.dumps({"patch": args.patch, "check": True, "scripts": plan}) + "\n"
            for script, item in zip(scripts, plan):
                if item["status"] == "pending":
                    if "fake:fail" in script.text:
                        raise RuntimeError(f"apply-sql 失败：执行 {script.path} 失败（已回滚该脚本）")
                    self.db["scripts"][f"{args.patch}/{script.path}"] = script.sha256
            return ""
        if args.action == "import-sqlite":
            assert (Path(mounts["/import"]) / Path(args.source).name).is_file()
            if self.db["users"]:
                raise RuntimeError("import-sqlite 失败：目标数据库不是空库")
            self.db["users"] = 2
            self.db["rows"].append("imported-v1")
            return ""
        raise AssertionError(args.action)


def make_source(base: Path, version: str, migrations=(1, 2, 3, 4), broken=False) -> Path:
    source = Path(tempfile.mkdtemp(prefix=f"src-{version}-", dir=base))
    write(source / "swr_builder/__main__.py", "print('app')\n")
    write(source / "swr_builder/webui/index.html", f"<html>{version}</html>")
    write(source / "swr_builder/qa.json", json.dumps({"version": version, "migrations": list(migrations),
                                                      "broken": broken}))
    write(source / "swr_builder/store/sqlscripts.py", (REPO / "swr_builder/store/sqlscripts.py").read_text())
    return source


def make_bundle(base: Path, version="2.0.0", *, seed_sql=None, contract=CONTRACT, migrations=(1, 2, 3, 4)) -> Path:
    bundle = Path(tempfile.mkdtemp(prefix=f"bundle-{version}-", dir=base))
    (bundle / "ops").mkdir()
    for name in ("manage.py", "release.py", "check_dependencies.py"):
        shutil.copy2(REPO / "deploy/offline" / name, bundle / "ops" / name)
    for name in ("swrctl", "config.yaml", "swr-builder.env.example"):
        shutil.copy2(REPO / "deploy/offline" / name, bundle / name)
    write(bundle / "部署文档.md", "部署文档")
    source = make_source(base, version, migrations)
    create(source, bundle / "releases", version, contract)
    with tarfile.open(bundle / "source.tar.gz", "w:gz") as tar:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                tar.add(path, arcname=path.relative_to(source).as_posix())
    write(bundle / "seed/certs/site-ca.crt", "CA")
    (bundle / "seed/images").mkdir()
    (bundle / "seed/images/jre-arm64.tar.gz").write_bytes(b"image")
    for relative, text in (seed_sql or {}).items():
        write(bundle / "seed/sql" / relative, text)
    return bundle


class Site:
    def __init__(self, base: Path):
        base.mkdir(parents=True, exist_ok=True)
        self.base = base
        self.root = base / "swr"
        self.docker = FakeDocker()

    def ctl(self, *argv, contract=CONTRACT):
        manage.run(["--root", str(self.root), *map(str, argv)], docker=self.docker, contract=contract,
                   sleep=lambda _seconds: None)

    def state(self) -> dict:
        return json.loads((self.root / "deploy.json").read_text())

    def install(self, bundle=None, *extra):
        bundle = bundle or make_bundle(self.base)
        self.ctl("install", "--bundle", bundle, "--image", APP_IMAGE, "--postgres-image", PG_IMAGE, *extra)
        return bundle

    def patch(self, version, *, code=True, migrations=(1, 2, 3, 4), sql=None, broken=False, base="",
              contract=CONTRACT) -> Path:
        source = make_source(self.base, version, migrations, broken)
        folder = None
        if sql:
            folder = Path(tempfile.mkdtemp(prefix="sql-", dir=self.base))
            for relative, text in sql.items():
                write(folder / relative, text)
        return create_patch(source, self.root / "updates", version, contract, code=code, sql=folder, base=base)

    def app(self) -> dict:
        return self.docker.containers["swr-builder"]

    def history(self) -> list:
        return [json.loads(line) for line in (self.root / "history.log").read_text().splitlines()]


@pytest.fixture
def site(tmp_path):
    return Site(tmp_path)


def test_install_with_bundled_database_seed_and_resources(site):
    site.install(make_bundle(site.base, seed_sql={"dml/001_dict.sql": "INSERT INTO dict VALUES (1);\n"}))
    state = site.state()
    assert state["schema"] == 2 and state["current"] == "2.0.0" and state["network"] == "swr"
    assert state["database"] == {"mode": "bundled", "container": "swr-postgres", "image": PG_IMAGE, "major": "16"}
    docker, config = site.docker, site.root / "config"
    assert "swr" in docker.networks
    database = docker.containers["swr-postgres"]
    assert database["running"] and database["mounts"][manage.PG_DATA] == str(site.root / "pgdata")
    # 随机口令同时写入数据库与应用的环境文件，600 权限；连接串指向数据库容器名
    postgres_env = manage.env_values((config / "postgres.env").read_text().splitlines())
    app_env = manage.env_values((config / "swr-builder.env").read_text().splitlines())
    assert postgres_env["POSTGRES_PASSWORD"] == app_env["BUILDER_DATABASE_PASSWORD"]
    assert len(app_env["BUILDER_DATABASE_PASSWORD"]) >= 32
    assert app_env["BUILDER_DATABASE_URL"] == "postgresql://swr@swr-postgres:5432/swr"
    for name in ("postgres.env", "swr-builder.env"):
        assert (config / name).stat().st_mode & 0o777 == 0o600
    # 先迁移、再铺底 SQL（记在 seed 名下），最后启动应用；证书与业务基础镜像一起导入
    assert [call[1] for call in docker.calls if call[0] == "task"] == ["status", "upgrade", "apply-sql"]
    assert docker.db["migrations"] == [1, 2, 3, 4] and set(docker.db["scripts"]) == {"seed/dml/001_dict.sql"}
    assert (config / "certs/site-ca.crt").read_text() == "CA" and docker.loaded == ["jre-arm64.tar.gz"]
    app = site.app()
    assert app["health"] == "healthy" and app["mounts"][manage.APP_CODE] == str(site.root / "releases/2.0.0")
    assert "0.0.0.0:8082:8080" in app["options"] and option_values(app["options"], "--network") == ["swr"]
    assert (site.root / "current").resolve() == site.root / "releases/2.0.0"
    assert (site.root / "runtime-image").read_text().strip() == APP_IMAGE
    assert site.history()[0]["action"] == "install"
    with pytest.raises(ValueError, match="已部署"):
        site.install()


def test_patch_with_code_migration_and_sql(site):
    site.install()
    site.docker.calls.clear()
    archive = site.patch("2.0.1", migrations=(1, 2, 3, 4, 5),
                         sql={"ddl/001_index.sql": "CREATE INDEX idx_t ON t (c);\n",
                              "dml/001_fix.sql": "UPDATE t SET c = 1;\n"})
    site.ctl("apply", archive)
    state, docker = site.state(), site.docker
    assert state["current"] == "2.0.1" and state["previous"] == "2.0.0"
    assert [(p["version"], p["code"], p["sql"]) for p in state["patches"]] == [
        ("2.0.1", True, ["ddl/001_index.sql", "dml/001_fix.sql"])]
    assert docker.db["migrations"] == [1, 2, 3, 4, 5]
    assert set(docker.db["scripts"]) == {"2.0.1/ddl/001_index.sql", "2.0.1/dml/001_fix.sql"}
    # 预检在停机前完成；停机后先导出数据库，再迁移、执行 SQL
    assert [c for c in docker.calls if c[0] in ("task", "stop", "exec")] == [
        ("task", "status"), ("task", "status"), ("task", "apply-sql"), ("stop", "swr-builder"),
        ("exec", "pg_dump"), ("task", "upgrade"), ("task", "apply-sql")]
    backup = Path(state["last_backup"])
    assert {p.name for p in backup.iterdir()} == {"database.dump", "files.tar.gz", "snapshot.json", "SHA256SUMS"}
    assert json.loads((backup / "database.dump").read_text())["migrations"] == [1, 2, 3, 4]
    assert (site.root / "patches/2.0.1/patch.json").is_file()
    assert (site.root / "patches/2.0.1/sql/ddl/001_index.sql").is_file()
    assert site.app()["mounts"][manage.APP_CODE] == str(site.root / "releases/2.0.1")
    with pytest.raises(ValueError, match="已经应用过"):
        site.ctl("apply", archive)


def test_sql_only_patch_keeps_the_code_version(site):
    site.install()
    site.ctl("apply", site.patch("2.0.0-sql1", code=False, sql={"dml/001_fix.sql": "UPDATE t SET c = 2;\n"}))
    state = site.state()
    assert state["current"] == "2.0.0" and state["previous"] is None
    assert state["patches"][0]["code"] is False and state["patches"][0]["sql"] == ["dml/001_fix.sql"]
    assert "2.0.0-sql1/dml/001_fix.sql" in site.docker.db["scripts"]
    assert not (site.root / "releases/2.0.0-sql1").exists()


def test_failed_sql_restores_database_files_and_previous_version(site):
    site.install()
    before = copy.deepcopy(site.docker.db)
    archive = site.patch("2.0.1", migrations=(1, 2, 3, 4, 5),
                         sql={"dml/001_ok.sql": "UPDATE t SET c = 1;\n",
                              "dml/002_bad.sql": "-- fake:fail\nUPDATE t SET c = 2;\n"})
    with pytest.raises(RuntimeError, match="002_bad.sql"):
        site.ctl("apply", archive)
    state = site.state()
    assert state["current"] == "2.0.0" and state["patches"] == []
    # 迁移 5 与已成功的 001_ok.sql 随数据库一起恢复；迁移写入的数据文件随 data/ 一起恢复
    assert site.docker.db == before
    assert not (site.root / "data/migrated-by-2.0.1.txt").exists()
    assert site.app()["health"] == "healthy"
    assert site.app()["mounts"][manage.APP_CODE] == str(site.root / "releases/2.0.0")
    assert site.history()[-1]["result"] == "failed"


def test_new_code_failing_health_check_is_rolled_back(site):
    site.install()
    with pytest.raises(RuntimeError, match="健康检查失败"):
        site.ctl("apply", site.patch("2.0.1", broken=True))
    assert site.state()["current"] == "2.0.0" and site.app()["health"] == "healthy"


def test_failed_backup_restarts_the_original_version(site):
    site.install()
    site.docker.dump_fails = True
    with pytest.raises(RuntimeError, match="No space left"):
        site.ctl("apply", site.patch("2.0.1", migrations=(1, 2, 3, 4, 5)))
    assert site.app()["running"] and site.app()["health"] == "healthy"
    assert site.state()["current"] == "2.0.0" and site.docker.db["migrations"] == [1, 2, 3, 4]
    assert not list((site.root / "backups").iterdir())  # 不完整的备份已删除


def test_bad_patches_are_rejected_before_any_downtime(site):
    site.install()
    site.docker.calls.clear()
    archive = site.patch("2.0.1")
    # 改动包内文件但不更新 patch.json
    work = site.base / "tampered"
    work.mkdir()
    extract(archive, work)
    write(work / "code/swr_builder/__main__.py", "print('changed')\n")
    tampered = site.root / "updates/tampered.tar.gz"
    with tarfile.open(tampered, "w:gz") as tar:
        for path in sorted(work.rglob("*")):
            if path.is_file():
                tar.add(path, arcname=path.relative_to(work).as_posix())
    with pytest.raises(ValueError, match="SHA256"):
        site.ctl("apply", tampered)
    # 包与旁边的 .sha256 不一致（传输损坏）
    damaged = site.root / "updates/damaged.tar.gz"
    shutil.copy2(archive, damaged)
    (site.root / "updates/damaged.tar.gz.sha256").write_text("0" * 64 + "  damaged.tar.gz\n")
    with pytest.raises(ValueError, match="不一致"):
        site.ctl("apply", damaged)
    with pytest.raises(ValueError, match="只能用于版本 1.9.9"):
        site.ctl("apply", site.patch("2.0.2", base="1.9.9"))
    with pytest.raises(ValueError, match="运行镜像未安装"):
        site.ctl("apply", site.patch("2.0.3", contract="d" * 64))
    # 同名脚本已在数据库中执行过、内容不同（例如上次失败后补丁被改过）
    site.docker.db["scripts"]["2.0.4/dml/001_fix.sql"] = "0" * 64
    with pytest.raises(RuntimeError, match="内容与本次不同"):
        site.ctl("apply", site.patch("2.0.4", sql={"dml/001_fix.sql": "UPDATE t SET c = 3;\n"}))
    outside = site.base / "outside.tar.gz"
    shutil.copy2(archive, outside)
    with pytest.raises(ValueError, match="部署目录内"):
        site.ctl("apply", outside)
    assert not [call for call in site.docker.calls if call[0] in ("stop", "rm", "exec")]
    assert site.app()["running"] and site.state()["current"] == "2.0.0"


def test_unfinished_tasks_block_maintenance(site):
    site.install()
    site.docker.db["active"] = {"jobs": 1, "deployments": 0}
    with pytest.raises(ValueError, match="1 个未结束的构建"):
        site.ctl("apply", site.patch("2.0.1"))
    for command in ("restart", "stop", "backup"):
        with pytest.raises(ValueError, match="未结束"):
            site.ctl(command)
    assert site.app()["running"]
    site.docker.db["active"] = {"jobs": 0, "deployments": 0}
    site.ctl("stop", "--with-db")
    assert not site.app()["running"] and not site.docker.containers["swr-postgres"]["running"]
    site.ctl("start")
    assert site.app()["running"] and site.docker.containers["swr-postgres"]["running"]


def test_rollback_and_restore(site, capsys):
    site.install()
    site.ctl("apply", site.patch("2.0.1"))
    site.ctl("rollback")
    assert site.state()["current"] == "2.0.0" and site.state()["previous"] == "2.0.1"
    site.ctl("rollback", "2.0.1")  # 也可以切到 releases/ 中保留的较新版本
    site.ctl("apply", site.patch("2.0.2", migrations=(1, 2, 3, 4, 5), sql={"dml/001_fix.sql": "UPDATE t SET c = 1;\n"}))
    before_patch = site.state()["last_backup"]
    # 2.0.1 不认识迁移 5：只回滚代码会起不来，必须用备份恢复
    with pytest.raises(ValueError, match=r"不认识数据库中的迁移 \[5\]"):
        site.ctl("rollback")
    capsys.readouterr()
    site.ctl("backups")
    listed = json.loads(capsys.readouterr().out)
    assert {"backup": before_patch, "version": "2.0.1"}.items() <= next(
        b for b in listed if b["backup"] == before_patch).items()
    site.ctl("restore", before_patch)
    state = site.state()
    assert state["current"] == "2.0.1" and [p["version"] for p in state["patches"]] == ["2.0.1"]
    assert site.docker.db["migrations"] == [1, 2, 3, 4]
    assert not any(key.startswith("2.0.2/") for key in site.docker.db["scripts"])
    assert site.history()[-1]["action"] == "restore" and site.history()[-1]["safety_backup"]
    # 被改动的备份不能用于恢复
    files = Path(before_patch) / "files.tar.gz"
    files.write_bytes(files.read_bytes() + b"x")
    with pytest.raises(ValueError, match="SHA256"):
        site.ctl("restore", before_patch)


def test_external_database_requires_backup_confirmation(site):
    settings = site.base / "site.env"
    write(settings, "BUILDER_DATABASE_URL=postgresql://swr@gauss.example:8000/swr\nBUILDER_DATABASE_PASSWORD=x\n")
    with pytest.raises(ValueError, match="BUILDER_DATABASE_URL"):
        site.install(None, "--database", "external")
    site.install(None, "--database", "external", "--env-file", settings)
    state = site.state()
    assert state["database"] == {"mode": "external"} and state["network"] == "bridge"
    assert "swr-postgres" not in site.docker.containers and not (site.root / "pgdata").exists()
    assert manage.env_values((site.root / "config/swr-builder.env").read_text().splitlines())[
        "BUILDER_DATABASE_URL"] == "postgresql://swr@gauss.example:8000/swr"
    # 只改代码、没有新迁移：不需要确认，备份中没有数据库
    site.ctl("apply", site.patch("2.0.1"))
    assert not (Path(site.state()["last_backup"]) / "database.dump").exists()
    archive = site.patch("2.0.2", sql={"dml/001_fix.sql": "UPDATE t SET c = 1;\n"})
    with pytest.raises(ValueError, match="--db-backup-confirmed"):
        site.ctl("apply", archive)
    site.ctl("apply", archive, "--db-backup-confirmed")
    assert site.state()["current"] == "2.0.2" and ("exec", "pg_dump") not in site.docker.calls
    # 外部数据库可以用 host 网络：不映射端口，平台直接监听安装时的地址与端口，健康检查访问同一地址
    hosted = Site(site.base / "hosted")
    hosted.install(None, "--database", "external", "--env-file", settings, "--network", "host",
                   "--bind", "10.0.0.5", "--port", "18082")
    app = hosted.app()
    assert "--publish" not in app["options"] and app["command"][-4:] == ["--host", "10.0.0.5", "--port", "18082"]
    assert "http://10.0.0.5:18082/healthz" in option_values(app["options"], "--health-cmd")[0]
    # 内置数据库模式下，环境文件不能自带数据库连接
    other = Site(site.base / "other")
    with pytest.raises(ValueError, match="不要在环境文件中设置"):
        other.install(None, "--env-file", settings)


def test_import_v1_data_into_a_new_directory(site):
    v1 = site.base / "swr-v1"
    write(v1 / "data/builder.db", "sqlite")
    (v1 / "data/secret.key").write_bytes(b"k" * 32)
    write(v1 / "data/jobs/1/build.log", "log")
    write(v1 / "config/config.yaml", "registry:\n  address: registry.example\n")
    write(v1 / "config/swr-builder.env", "SWR_PASSWORD=old\n# 注释\nBUILDER_ADMIN_PASSWORD=x\n")
    write(v1 / "config/certs/registry-ca.crt", "V1CA")
    # v1 的容器默认也叫 swr-builder
    write(v1 / "deploy.json", json.dumps({"schema": 1, "container": "swr-builder"}))
    site.docker.containers["swr-builder"] = {"image": APP_IMAGE, "labels": {"swr.deployment-root": str(v1)},
                                             "running": True, "health": "healthy", "mounts": {}, "options": []}
    with pytest.raises(ValueError, match="仍在运行"):
        site.install(None, "--import-v1", v1)
    site.docker.containers["swr-builder"]["running"] = False
    with pytest.raises(ValueError, match="不重叠"):
        Site(v1).install(None, "--import-v1", v1)
    # 不导入 v1 时，同名容器一律拒绝
    fresh = Site(site.base / "fresh")
    fresh.docker = site.docker
    with pytest.raises(ValueError, match="已存在"):
        fresh.install()
    site.install(None, "--import-v1", v1)
    assert site.app()["labels"]["swr.deployment-root"] == str(site.root)
    root = site.root
    assert (root / "data/secret.key").read_bytes() == b"k" * 32 and (root / "data/jobs/1/build.log").is_file()
    assert not (root / "data/builder.db").exists() and (v1 / "data/builder.db").read_text() == "sqlite"
    assert (root / "config/config.yaml").read_text().startswith("registry:")
    values = manage.env_values((root / "config/swr-builder.env").read_text().splitlines())
    assert values["SWR_PASSWORD"] == "old" and values["BUILDER_DATABASE_URL"].endswith("@swr-postgres:5432/swr")
    assert (root / "config/certs/registry-ca.crt").read_text() == "V1CA"
    # 在应用首次启动（创建 admin）之前导入，导入的账号保留
    assert [c[1] for c in site.docker.calls if c[0] == "task"] == ["status", "upgrade", "import-sqlite"]
    assert site.docker.db["users"] == 2 and "imported-v1" in site.docker.db["rows"]


def test_failed_install_is_cleaned_up_and_can_be_retried(site):
    bundle = make_bundle(site.base)
    site.docker.app_fails = True
    with pytest.raises(RuntimeError, match="健康检查失败"):
        site.install(bundle)
    assert not site.docker.containers
    failed = list(site.root.glob("failed-install-*"))
    assert len(failed) == 1 and (failed[0] / "pgdata/PG_VERSION").is_file() and (failed[0] / "deploy.json").is_file()
    assert not (site.root / "deploy.json").exists() and not (site.root / "config").exists()
    site.docker.app_fails = False
    site.install(bundle)
    assert site.state()["current"] == "2.0.0" and site.app()["health"] == "healthy"


def test_full_package_upgrade_rollback_and_postgres_versions(site):
    seed = {"dml/001_dict.sql": "INSERT INTO dict VALUES (1);\n"}
    site.install(make_bundle(site.base, seed_sql=seed))
    contract, image, minor = "e" * 64, "sha256:" + "f" * 64, "sha256:" + "9" * 64
    site.docker.add_image(image)
    site.docker.add_image(minor, major="16")
    bundle = make_bundle(site.base, "2.1.0", contract=contract,
                         seed_sql={**seed, "dml/002_more.sql": "INSERT INTO dict VALUES (2);\n"})
    site.ctl("upgrade", "--bundle", bundle, "--image", image, "--postgres-image", minor, contract=contract)
    state = site.state()
    assert state["current"] == "2.1.0" and state["image"] == image
    assert state["runtimes"] == {CONTRACT: APP_IMAGE, contract: image}
    assert state["database"]["image"] == minor and site.docker.containers["swr-postgres"]["image"] == minor
    assert set(site.docker.db["scripts"]) == {"seed/dml/001_dict.sql", "seed/dml/002_more.sql"}
    assert (site.root / "source-versions/2.1.0/swr_builder/qa.json").is_file()
    # 跨运行镜像回滚：代码与镜像一起切回
    site.ctl("rollback", contract=contract)
    assert site.state()["current"] == "2.0.0" and site.state()["image"] == APP_IMAGE
    site.ctl("rollback", "2.1.0")
    assert site.state()["image"] == image
    # PostgreSQL 主版本变化不能直接替换数据目录
    major = "sha256:" + "7" * 64
    site.docker.add_image(major, major="17")
    with pytest.raises(ValueError, match="主版本 17"):
        site.ctl("upgrade", "--bundle", make_bundle(site.base, "2.1.1", contract=contract),
                 "--image", image, "--postgres-image", major, contract=contract)
    # 已执行过的铺底脚本被修改：停机前拒绝
    changed = make_bundle(site.base, "2.1.2", contract=contract, seed_sql={"dml/001_dict.sql": "INSERT INTO dict VALUES (9);\n"})
    with pytest.raises(RuntimeError, match="内容与本次不同"):
        site.ctl("upgrade", "--bundle", changed, "--image", image, contract=contract)
    assert site.app()["running"] and site.state()["current"] == "2.1.0"


def test_v1_deployment_record_is_not_reused(site):
    write(site.root / "deploy.json", json.dumps({"schema": 1, "current": "20261109-r1"}))
    with pytest.raises(ValueError, match="v1 的部署"):
        site.ctl("status")
