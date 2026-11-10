#!/usr/bin/env python3
"""在隔离 Docker 28 daemon 中验证冷导入与镜像自包含启动，不挂宿主 Docker socket。"""
import argparse
import json
import platform
import subprocess
import tarfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("bundle", type=Path)
parser.add_argument("--report", type=Path, required=True)
args = parser.parse_args()
bundle = args.bundle.resolve()
tag = (bundle / "runtime-image").read_text().strip()
engine = ROOT / "artifacts/arm64/vendor/engine"
engine.mkdir(exist_ok=True)
with tarfile.open(ROOT / "artifacts/arm64/vendor/docker-28.5.2-aarch64.tgz", "r:gz") as tar:
    for member in tar.getmembers():
        if member.isfile() and member.name.startswith("docker/") and ".." not in Path(member.name).parts:
            destination = engine / Path(member.name).name
            destination.write_bytes(tar.extractfile(member).read())
            destination.chmod(0o755)
name = "swr-docker28-qa-" + uuid.uuid4().hex[:8]
volume = name + "-data"
report = {"result": "running", "daemon": "28.5.2", "checks": []}


def run(*command, okay=True):
    result = subprocess.run([str(x) for x in command], text=True, capture_output=True)
    if okay and result.returncode:
        raise RuntimeError(result.stderr[-3000:] + result.stdout[-1000:])
    return result


def inner(*command, okay=True):
    return run("docker", "exec", name, "/engine/docker", *command, okay=okay)


try:
    run("docker", "volume", "create", "--label", "swr.qa=true", volume)
    run("docker", "run", "-d", "--name", name, "--platform", "linux/arm64", "--privileged",
        "--network", "none", "--no-healthcheck",
        "--mount", f"type=volume,src={volume},dst=/var/lib/docker",
        "--mount", f"type=bind,src={engine},dst=/engine,readonly",
        "--mount", f"type=bind,src={bundle},dst=/bundle,readonly",
        "--env", "PATH=/engine:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "--entrypoint", "/engine/dockerd", tag,
        "--host", "unix:///var/run/docker.sock", "--iptables=false", "--ip6tables=false",
        "--bridge=none", "--storage-driver=vfs")
    for _ in range(90):
        ready = inner("info", "--format", "{{.ServerVersion}} {{.Architecture}}", okay=False)
        if ready.returncode == 0:
            assert "28.5.2" in ready.stdout and ("aarch64" in ready.stdout or "arm64" in ready.stdout)
            break
        if run("docker", "inspect", name, "--format", "{{.State.Running}}").stdout.strip() != "true":
            raise RuntimeError(run("docker", "logs", name).stdout[-3000:])
        time.sleep(1)
    else:
        raise RuntimeError("isolated Docker 28 daemon did not become ready")
    assert inner("image", "ls", "-q").stdout.strip() == ""
    inner("load", "-i", "/bundle/images/swr-builder-arm64.tar.gz")
    metadata = json.loads(inner("image", "inspect", tag).stdout)[0]
    assert metadata["Architecture"] == "arm64"
    report["loaded_image_id"] = metadata["Id"]
    report["checks"].append("cold_import_into_empty_docker28_classic_store")
    if platform.machine() not in ("aarch64", "arm64"):
        # QEMU 用户态模拟不等价于原生 ARM64 runc 的 namespace/re-exec。
        # x86 构建机只证明 Docker 28 冷导入；应用执行由主部署验收脚本验证。
        report["result"] = "passed"
        report["scope"] = "cold import only; native ARM64 Docker 28 application boot requires target-host acceptance"
        print(json.dumps(report, indent=2))
        raise SystemExit(0)
    inner("run", "-d", "--name", "standalone", "--network", "none", metadata["Id"])
    for _ in range(90):
        status = inner("inspect", "standalone", "--format", "{{.State.Health.Status}}").stdout.strip()
        if status == "healthy":
            break
        time.sleep(2)
    else:
        raise RuntimeError("standalone application did not become healthy")
    inner("exec", "standalone", "python", "-c",
          "import urllib.request,platform,yaml; assert platform.machine()=='aarch64'; "
          "assert urllib.request.urlopen('http://127.0.0.1:8080/healthz').status==200; "
          "assert 'assets/index-' in urllib.request.urlopen('http://127.0.0.1:8080/login').read().decode()")
    report["checks"].append("self_contained_image_starts_offline_on_docker28")
    report["result"] = "passed"
    print(json.dumps(report, indent=2))
except Exception as exc:
    report["result"] = "failed"
    report["error"] = str(exc)
    raise
finally:
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    run("docker", "rm", "-f", name, okay=False)
    run("docker", "volume", "rm", volume, okay=False)
