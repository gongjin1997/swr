#!/usr/bin/env python3
"""用额外 Python 模块模拟运行依赖升级，实测完整包升级（upgrade.sh）与跨运行镜像回滚；数据保存在内置 PostgreSQL。

    python3 scripts/verify-arm64-runtime-upgrade.py <完整包目录> <verify-arm64-deployment.py 的报告> --report <输出>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/offline"))
from release import create, digest, extract  # noqa: E402


def run(*args):
    result = subprocess.run([str(a) for a in args], text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stdout[-2000:]} {result.stderr[-3000:]}")
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("previous_report", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    old = json.loads(args.previous_report.read_text())
    assert old["result"] == "passed", "先完成基础部署验收"
    root, name, db_name = Path(old["root"]), old["container"], old["database"]
    base = args.bundle.resolve()
    tag = "swr-builder:arm64-qa-runtime-" + root.name.split("-")[-1].lower()
    report = {"checks": [], "result": "running"}

    def ctl(*command):
        return run("sudo", "-n", root / "swrctl", *command)

    def sql(statement):
        return run("docker", "exec", db_name, "psql", "-U", "swr", "-d", "swr", "-At", "-c", statement)

    def probe():
        return {"users": sql("SELECT string_agg(username || ':' || password_hash, ',' ORDER BY id) FROM users"),
                "sentinel": sql("SELECT value FROM offline_qa_sentinel"),
                "key": run("docker", "exec", name, "python", "-c",
                           "import hashlib; from pathlib import Path; "
                           "print(hashlib.sha256(Path('/var/lib/swr/secret.key').read_bytes()).hexdigest())")}

    try:
        ctl("start")
        status = json.loads(ctl("status"))
        base_tag = (base / "runtime-image").read_text().strip()
        assert run("docker", "image", "inspect", base_tag, "--format", "{{.Id}}") == status["image"]
        before = probe()
        with tempfile.TemporaryDirectory(prefix="swr-runtime-upgrade-qa-") as temp:
            workspace = Path(temp)
            bundle = workspace / "bundle"
            bundle.mkdir()
            for child in ("ops", "releases", "images"):
                (bundle / child).mkdir()
            shutil.copytree(base / "ops", bundle / "ops", dirs_exist_ok=True)
            for child in ("upgrade.sh", "swrctl", "部署文档.md"):
                shutil.copy2(base / child, bundle / child)
            lock = json.loads((ROOT / "deploy/offline/runtime-lock.json").read_text())
            module = "VALUE = 'new-arm64-runtime-dependency'\n"
            lock["qa_extra_dependency_sha256"] = hashlib.sha256(module.encode()).hexdigest()
            (workspace / "runtime-lock.json").write_text(json.dumps(lock, sort_keys=True, indent=2) + "\n")
            (workspace / "swr_offline_upgrade_probe.py").write_text(module)
            (workspace / "Dockerfile").write_text(
                f"FROM {base_tag}\n"
                "COPY runtime-lock.json /opt/swr-runtime/runtime-lock.json\n"
                "COPY swr_offline_upgrade_probe.py /usr/local/lib/python3.11/site-packages/swr_offline_upgrade_probe.py\n"
                "RUN sha256sum /opt/swr-runtime/runtime-lock.json | cut -d ' ' -f 1 > /opt/swr-runtime/contract\n")
            run("docker", "build", "--platform", "linux/arm64", "--network", "none", "-t", tag, workspace)
            source = workspace / "source"
            source.mkdir()
            extract(base / "source.tar.gz", source)
            shutil.copy2(workspace / "runtime-lock.json", source / "deploy/offline/runtime-lock.json")
            init = source / "swr_builder/__init__.py"
            init.write_text(init.read_text() + "\nimport swr_offline_upgrade_probe\n")
            # 完整包里的代码包带编译好的前端
            with tarfile.open(next((base / "releases").glob("*.tar.gz")), "r:gz") as code:
                for member in code.getmembers():
                    if member.isfile() and member.name.startswith("swr_builder/webui/"):
                        target = source / member.name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(code.extractfile(member).read())
            create(source, bundle / "releases", "qa-runtime-r4", digest(workspace / "runtime-lock.json"))
            with tarfile.open(bundle / "source.tar.gz", "w:gz") as tar:
                for path in sorted(source.rglob("*")):
                    if path.is_file():
                        tar.add(path, arcname=str(path.relative_to(source)))
            (bundle / "runtime-image").write_text(tag + "\n")
            with (bundle / "images/swr-builder-arm64.tar.gz").open("wb") as output:
                save = subprocess.Popen(["docker", "save", "--platform", "linux/arm64", tag], stdout=subprocess.PIPE)
                gzip = subprocess.Popen(["gzip", "-1"], stdin=save.stdout, stdout=output)
                save.stdout.close()
                assert gzip.wait() == 0 and save.wait() == 0
            (bundle / "SHA256SUMS").write_text("".join(
                f"{digest(p)}  {p.relative_to(bundle).as_posix()}\n"
                for p in sorted(bundle.rglob("*")) if p.is_file()))
            # 先验证代码包 / 补丁拒绝新的运行依赖标识，随后由完整包正式升级。
            run("docker", "cp", bundle / "releases/swr-code-qa-runtime-r4.tar.gz", f"{name}:/var/lib/swr/qa-runtime.tar.gz")
            mismatch = subprocess.run(["sudo", "-n", str(root / "swrctl"), "apply", str(root / "data/qa-runtime.tar.gz")],
                                      text=True, capture_output=True)
            assert mismatch.returncode != 0 and "运行依赖" in mismatch.stderr + mismatch.stdout
            report["checks"].append("code_package_rejects_new_runtime_contract")
            run("sh", bundle / "upgrade.sh", root)
            updated = json.loads(ctl("status"))
            assert updated["image"] != status["image"] and updated["version"] == "qa-runtime-r4"
            assert probe() == before
            assert run("docker", "exec", name, "python", "-c",
                       "import swr_offline_upgrade_probe; print(swr_offline_upgrade_probe.VALUE)") == \
                "new-arm64-runtime-dependency"
            ctl("verify")
            report["checks"].append("full_bundle_installs_new_runtime_dependency_and_preserves_data")
            ctl("rollback")
            rolled_back = json.loads(ctl("status"))
            assert rolled_back["image"] == status["image"] and rolled_back["version"] == status["version"]
            assert probe() == before
            ctl("verify")
            report["checks"].append("rollback_restores_previous_runtime_image_and_code")
        report["result"] = "passed"
        print(json.dumps(report, indent=2))
    except Exception as exc:
        report["result"] = "failed"
        report["error"] = str(exc)
        raise
    finally:
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        subprocess.run(["docker", "rm", "-f", name, db_name], capture_output=True)
        subprocess.run(["docker", "network", "rm", old["network"]], capture_output=True)
        # QA 镜像保留，便于复核已登记的跨镜像回滚关系。


if __name__ == "__main__":
    main()
