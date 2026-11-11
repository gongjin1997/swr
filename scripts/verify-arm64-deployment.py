#!/usr/bin/env python3
"""在隔离的 ARM64 容器中验收真实交付包，不接触已有平台服务。"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path


def run(*args, env=None, okay=True):
    result = subprocess.run([str(a) for a in args], text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, env=env)
    if okay and result.returncode:
        raise RuntimeError(f"{args[0]} {args[1:3]} failed: {result.stdout[-4000:]}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    root = Path(tempfile.mkdtemp(prefix="swr-arm64-qa-"))
    suffix = root.name.removeprefix("swr-arm64-qa-")
    name = "swr-arm64-qa-" + suffix
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    report = {"root": str(root), "container": name, "port": port, "checks": [],
              "image": json.loads((bundle / "manifest.json").read_text())["image_id"]}

    def passed(check):
        report["checks"].append(check)
        print("PASS " + check, flush=True)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    def ctl(*command, okay=True):
        return run("sudo", "-n", root / "swrctl", *command, okay=okay)

    def app(code):
        return run("docker", "exec", name, "python", "-c", code).stdout.strip()

    def helper(code):
        image = (bundle / "runtime-image").read_text().strip()
        return run("docker", "run", "--rm", "--platform", "linux/arm64", "--network", "none",
                   "--mount", f"type=bind,src={root},dst={root}", "--entrypoint", "python",
                   image, "-c", code).stdout.strip()

    def persisted():
        return json.loads(app("import sqlite3,hashlib,json; from pathlib import Path; "
                              "db=sqlite3.connect('/var/lib/swr/builder.db'); "
                              "print(json.dumps({'users':db.execute('select username,password_hash from users order by id').fetchall(),"
                              "'value':db.execute('select value from offline_qa_sentinel').fetchone()[0],"
                              "'key':hashlib.sha256(Path('/var/lib/swr/secret.key').read_bytes()).hexdigest()}))"))

    try:
        env = dict(os.environ, SWR_ALLOW_EMULATION="1", SWR_CONTAINER_NAME=name,
                   SWR_BIND_ADDRESS="127.0.0.1", SWR_DOCKER_NETWORK="none")
        run("sh", bundle / "install.sh", root, port, env=env)
        status = json.loads(ctl("status").stdout)
        assert status["health"] == "healthy", status
        assert json.loads(run("docker", "inspect", name).stdout)[0]["HostConfig"]["NetworkMode"] == "none"
        passed("offline_install_network_disabled")
        assert app("import platform; print(platform.machine())") == "aarch64"
        ctl("verify")
        passed("arm64_runtime_and_dependency_imports")
        # 密码只在 ARM64 容器内读取和使用，绝不写入测试报告。
        app("import httpx; from pathlib import Path; "
            "text=Path('/var/lib/swr/admin-initial-password.txt').read_text(); "
            "password=text.split('初始密码：',1)[1].splitlines()[0]; "
            "c=httpx.Client(base_url='http://127.0.0.1:8080',headers={'X-Requested-With':'XMLHttpRequest'}); "
            "r=c.post('/api/auth/login',json={'username':'admin','password':password}); assert r.status_code==200,r.text; "
            "r=c.put('/api/account/password',json={'old_password':password,'new_password':'OfflineQaOnly123!'}); assert r.status_code==200,r.text; "
            "r=c.put('/api/account/profile',json={'display_name':'ARM64 offline QA','email':''}); assert r.status_code==200,r.text; "
            "assert c.get('/api/info').json()['docker']['available'] is True; print('authenticated')")
        app("import sqlite3; db=sqlite3.connect('/var/lib/swr/builder.db'); "
            "db.execute('create table offline_qa_sentinel(value text)'); "
            "db.execute(\"insert into offline_qa_sentinel values ('preserve-me')\"); db.commit()")
        before = persisted()
        passed("login_password_change_profile_and_docker_socket")
        original = status["version"]
        marker = "arm64-offline-code-update"
        helper(f"from pathlib import Path; p=Path({str(root / 'source/swr_builder/__init__.py')!r}); "
               f"p.write_text(p.read_text().replace('1.3.0-dev.1',{marker!r})); "
               f"p=Path({str(root / 'source/web/index.html')!r}); "
               "p.write_text(p.read_text().replace('<title>', '<title>OFFLINE QA '))")
        ctl("build-update", root / "source", "qa-code-r2")
        passed("frontend_and_backend_code_package_built_without_network")
        ctl("update", root / "updates/swr-code-qa-code-r2.tar.gz")
        assert persisted() == before
        assert app("import swr_builder; print(swr_builder.__version__)") == marker
        html = app("import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/login').read().decode())")
        assert "OFFLINE QA" in html
        after = json.loads(ctl("status").stdout)
        assert after["image"] == status["image"], "code update changed the image"
        passed("code_only_update_preserves_accounts_database_secret_and_image")
        ctl("rollback")
        assert json.loads(ctl("status").stdout)["version"] == original
        assert persisted() == before
        passed("rollback_preserves_current_business_data")
        ctl("backup")
        state = json.loads(helper(f"from pathlib import Path; print(Path({str(root / 'deploy.json')!r}).read_text())"))
        backup = helper(f"from pathlib import Path; print(sorted(Path({str(root / 'backups')!r}).glob('backup-*.tar.gz'))[-1])")
        app("import sqlite3; db=sqlite3.connect('/var/lib/swr/builder.db'); "
            "db.execute(\"update offline_qa_sentinel set value='changed-after-backup'\"); db.commit()")
        ctl("restore", backup)
        assert persisted() == before
        passed("consistent_backup_and_explicit_restore")
        # 新版在启动时先改数据再失败，验证自动回滚连同数据一起恢复。
        helper(f"import sys; from pathlib import Path; sys.path.insert(0,{str(root / 'ops')!r}); from release import create; "
               f"p=Path({str(root / 'source/swr_builder/__main__.py')!r}); "
               "p.write_text(\"import sqlite3\\ndb=sqlite3.connect('/var/lib/swr/builder.db')\\ndb.execute(\\\"update offline_qa_sentinel set value='bad-migration'\\\")\\ndb.commit()\\nraise RuntimeError('QA startup failure')\\n\"); "
               f"create(Path({str(root / 'source')!r}),Path({str(root / 'updates')!r}),'qa-broken-r3',Path('/opt/swr-runtime/contract').read_text().strip())")
        result = ctl("update", root / "updates/swr-code-qa-broken-r3.tar.gz", okay=False)
        assert result.returncode != 0
        assert json.loads(ctl("status").stdout)["version"] == original
        assert persisted() == before
        passed("failed_startup_restores_code_config_and_pre_upgrade_database")
        # 损坏发布包必须在停服务前拒绝，当前实例仍然健康。
        helper(f"from pathlib import Path; p=Path({str(root / 'updates/bad.tar.gz')!r}); p.write_bytes(b'not-an-archive')")
        assert ctl("update", root / "updates/bad.tar.gz", okay=False).returncode != 0
        ctl("verify")
        passed("corrupt_package_rejected_without_service_change")
        # 未结束的任务必须阻止重启；不让测试任务进入实际 worker。
        app("import sqlite3; db=sqlite3.connect('/var/lib/swr/builder.db'); "
            "db.execute(\"insert into jobs(status,app_name,project_id,created_at) select 'queued','offline-qa-pending',id,datetime('now') from projects where slug='default'\"); db.commit()")
        rejected = ctl("restart", okay=False)
        assert rejected.returncode != 0 and "未结束" in rejected.stdout
        app("import sqlite3; db=sqlite3.connect('/var/lib/swr/builder.db'); "
            "db.execute(\"delete from jobs where app_name='offline-qa-pending'\"); db.commit()")
        passed("unfinished_jobs_block_restart")
        helper(f"import json; from pathlib import Path; p=Path({str(root / 'deploy.json')!r}); "
               "s=json.loads(p.read_text()); s['network']='bridge'; p.write_text(json.dumps(s))")
        ctl("restart")
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
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        run("docker", "rm", "-f", name, okay=False)
        # 保留临时数据与报告以供诊断，自动删除的只有本次 QA 容器和网络。


if __name__ == "__main__":
    main()
