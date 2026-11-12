#!/usr/bin/env python3
"""用真实 Docker 验收 v2 全量离线包与补丁包；使用独立的部署目录、容器名、网络和端口，不接触已有平台服务。

    python3 scripts/verify-arm64-deployment.py <解开的完整包目录> --report artifacts/arm64/deployment-qa.json

需要能执行 docker 与 sudo -n（swrctl 以 root 运行）。ARM64 主机直接执行；x86 构建机需先安装 ARM64 binfmt
（见部署文档「构建」），脚本会设置 SWR_ALLOW_EMULATION=1。QEMU 下通过只说明包内程序与流程可执行，
不能替代目标 ARM64 主机、现场仓库与集群的验收。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import tempfile
import urllib.request
from pathlib import Path


def run(*args, env=None, okay=True):
    result = subprocess.run([str(a) for a in args], text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, env=env)
    if okay and result.returncode:
        raise RuntimeError(f"{args[0]} {args[1:3]} failed: {result.stdout[-4000:]}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    root = Path(tempfile.mkdtemp(prefix="swr-arm64-qa-"))
    suffix = root.name.removeprefix("swr-arm64-qa-").lower()
    name, db_name, network = f"swr-arm64-qa-{suffix}", f"swr-arm64-qa-db-{suffix}", f"swr-arm64-qa-{suffix}"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    manifest = json.loads((bundle / "manifest.json").read_text())
    report = {"root": str(root), "container": name, "database": db_name, "network": network, "port": port,
              "checks": [], "image": manifest["image_id"], "postgres": manifest["postgres_image_id"]}

    def passed(check):
        report["checks"].append(check)
        print("PASS " + check, flush=True)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    def ctl(*command, okay=True):
        return run("sudo", "-n", root / "swrctl", *command, okay=okay)

    def state():
        return json.loads(ctl("status").stdout)

    def app(code):
        return run("docker", "exec", name, "python", "-c", code).stdout.strip()

    def sql(statement):
        return run("docker", "exec", db_name, "psql", "-U", "swr", "-d", "swr", "-At", "-v", "ON_ERROR_STOP=1",
                   "-c", statement).stdout.strip()

    def helper(code):
        """在运行镜像中（关闭网络）以 root 读写 QA 部署目录。"""
        image = (root / "runtime-image").read_text().strip()
        return run("docker", "run", "--rm", "--platform", "linux/arm64", "--network", "none",
                   "--mount", f"type=bind,src={root},dst={root}", "--entrypoint", "python",
                   image, "-c", code).stdout.strip()

    def write(path, text):
        helper(f"from pathlib import Path; p=Path({str(path)!r}); p.parent.mkdir(parents=True, exist_ok=True); "
               f"p.write_text({text!r})")

    def persisted():
        return {"users": sql("SELECT string_agg(username || ':' || password_hash, ',' ORDER BY id) FROM users"),
                "sentinel": sql("SELECT value FROM offline_qa_sentinel"),
                "key": app("import hashlib; from pathlib import Path; "
                           "print(hashlib.sha256(Path('/var/lib/swr/secret.key').read_bytes()).hexdigest())")}

    try:
        env = dict(os.environ, SWR_ALLOW_EMULATION="1", SWR_CONTAINER_NAME=name, SWR_DB_CONTAINER_NAME=db_name,
                   SWR_DOCKER_NETWORK=network, SWR_BIND_ADDRESS="127.0.0.1")
        run("sh", bundle / "install.sh", root, port, env=env)
        status = state()
        assert status["health"] == "healthy" and status["database"]["health"] == "healthy", status
        assert app("import platform; print(platform.machine())") == "aarch64"
        ctl("verify")
        passed("offline_install_with_bundled_postgresql")
        # 密码只在 ARM64 容器内读取和使用，绝不写入测试报告。
        app("import httpx; from pathlib import Path; "
            "text=Path('/var/lib/swr/admin-initial-password.txt').read_text(); "
            "password=text.split('初始密码：',1)[1].splitlines()[0]; "
            "c=httpx.Client(base_url='http://127.0.0.1:8080',headers={'X-Requested-With':'XMLHttpRequest'}); "
            "r=c.post('/api/auth/login',json={'username':'admin','password':password}); assert r.status_code==200,r.text; "
            "r=c.put('/api/account/password',json={'old_password':password,'new_password':'OfflineQaOnly123!'}); assert r.status_code==200,r.text; "
            "r=c.put('/api/account/profile',json={'display_name':'ARM64 offline QA','email':''}); assert r.status_code==200,r.text; "
            "assert c.get('/api/info').json()['docker']['available'] is True; print('authenticated')")
        sql("CREATE TABLE offline_qa_sentinel(value text); INSERT INTO offline_qa_sentinel VALUES ('preserve-me')")
        before = persisted()
        passed("login_password_change_profile_and_docker_socket")
        original = status["version"]

        # 在目标机离线制作“代码 + SQL”补丁：改后端版本号和页面标题，新建一张表并写一行
        marker = "arm64-offline-patch"
        helper("import re; from pathlib import Path; "
               f"p=Path({str(root / 'source/swr_builder/__init__.py')!r}); "
               f"p.write_text(re.sub(r'__version__ = \"[^\"]+\"', '__version__ = \"{marker}\"', p.read_text())); "
               f"p=Path({str(root / 'source/web/index.html')!r}); "
               "p.write_text(p.read_text().replace('<title>', '<title>OFFLINE QA '))")
        write(root / "qa-sql/ddl/001_table.sql", "CREATE TABLE offline_qa_patch (id BIGSERIAL PRIMARY KEY, note TEXT);\n")
        write(root / "qa-sql/dml/001_row.sql", "INSERT INTO offline_qa_patch(note) VALUES ('code-and-sql');\n")
        ctl("build-patch", "qa-r2", "--sql", root / "qa-sql", "--description", "QA 代码与 SQL 补丁")
        passed("code_and_sql_patch_built_on_target_without_network")
        ctl("apply", root / "updates/swr-patch-qa-r2.tar.gz")
        assert persisted() == before
        assert app("import swr_builder; print(swr_builder.__version__)") == marker
        html = app("import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/login').read().decode())")
        assert "OFFLINE QA" in html
        assert sql("SELECT note FROM offline_qa_patch") == "code-and-sql"
        after = state()
        assert after["image"] == status["image"] and after["version"] == "qa-r2" and after["patches"] == ["qa-r2"]
        passed("code_and_sql_patch_preserves_accounts_data_secret_and_image")

        write(root / "qa-sql2/dml/001_more.sql", "INSERT INTO offline_qa_patch(note) VALUES ('sql-only');\n")
        ctl("build-patch", "qa-r2-sql1", "--no-code", "--sql", root / "qa-sql2")
        ctl("apply", root / "updates/swr-patch-qa-r2-sql1.tar.gz")
        assert sql("SELECT count(*) FROM offline_qa_patch") == "2" and state()["version"] == "qa-r2"
        assert ctl("apply", root / "updates/swr-patch-qa-r2-sql1.tar.gz", okay=False).returncode != 0
        passed("sql_only_patch_keeps_code_version_and_runs_once")

        ctl("rollback")
        assert state()["version"] == original and persisted() == before
        passed("rollback_switches_code_and_keeps_current_data")

        ctl("backup")
        backup = max(json.loads(ctl("backups").stdout), key=lambda item: item["backup"])["backup"]
        sql("UPDATE offline_qa_sentinel SET value = 'changed-after-backup'")
        ctl("restore", backup)
        assert persisted() == before and state()["version"] == original
        passed("consistent_backup_and_explicit_restore_including_database")

        # 第二个脚本失败：第一个脚本已经提交的改动随数据库一起恢复
        write(root / "qa-bad/dml/001_change.sql", "UPDATE offline_qa_sentinel SET value = 'bad-patch';\n")
        write(root / "qa-bad/dml/002_fail.sql", "INSERT INTO offline_qa_missing VALUES (1);\n")
        ctl("build-patch", "qa-bad-r3", "--no-code", "--sql", root / "qa-bad")
        result = ctl("apply", root / "updates/swr-patch-qa-bad-r3.tar.gz", okay=False)
        assert result.returncode != 0 and "002_fail.sql" in result.stdout, result.stdout[-2000:]
        assert state()["version"] == original and persisted() == before
        passed("failed_patch_sql_restores_database_files_and_version")

        # 新版本启动时先改数据库再退出：自动恢复代码、配置与数据库
        helper(f"from pathlib import Path; p=Path({str(root / 'source/swr_builder/__main__.py')!r}); "
               "p.write_text(\"import sys\\nif sys.argv[1:2] != ['db']:\\n"
               "    import os, psycopg\\n"
               "    with psycopg.connect(os.environ['BUILDER_DATABASE_URL'], password=os.environ['BUILDER_DATABASE_PASSWORD'], autocommit=True) as c:\\n"
               "        c.execute(\\\"UPDATE offline_qa_sentinel SET value = 'bad-startup'\\\")\\n"
               "    raise SystemExit('QA startup failure')\\n\" + p.read_text())")
        ctl("build-patch", "qa-broken-r4")
        result = ctl("apply", root / "updates/swr-patch-qa-broken-r4.tar.gz", okay=False)
        assert result.returncode != 0
        assert state()["version"] == original and persisted() == before
        passed("failed_startup_restores_code_config_and_database")

        # 损坏的包在停服务前拒绝，当前实例仍然健康
        helper(f"from pathlib import Path; Path({str(root / 'updates/bad.tar.gz')!r}).write_bytes(b'not-an-archive')")
        assert ctl("apply", root / "updates/bad.tar.gz", okay=False).returncode != 0
        ctl("verify")
        passed("corrupt_package_rejected_without_service_change")

        # 未结束的任务阻止重启；不让测试任务进入实际 worker
        sql("INSERT INTO jobs(project_id, app_name, status, created_at) "
            "SELECT id, 'offline-qa-pending', 'queued', now() FROM projects WHERE slug = 'default'")
        rejected = ctl("restart", okay=False)
        assert rejected.returncode != 0 and "未结束" in rejected.stdout
        sql("DELETE FROM jobs WHERE app_name = 'offline-qa-pending'")
        passed("unfinished_jobs_block_restart")

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=10) as response:
            assert json.load(response)["ok"] is True
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/login", timeout=10) as response:
            assert '<div id="app">' in response.read().decode()
        passed("host_port_mapping_and_login_page")
        report["result"] = "passed"
    except Exception as exc:
        report["result"] = "failed"
        report["error"] = str(exc)
        raise
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        if os.environ.get("SWR_QA_KEEP") != "1":
            run("docker", "rm", "-f", name, db_name, okay=False)
            run("docker", "network", "rm", network, okay=False)
        # 只删除本次 QA 的容器与网络；临时部署目录（含数据库数据）与报告保留，
        # verify-arm64-runtime-upgrade.py 用 swrctl start 在同一目录上继续验收。


if __name__ == "__main__":
    main()
