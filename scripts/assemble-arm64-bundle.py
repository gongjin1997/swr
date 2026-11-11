#!/usr/bin/env python3
"""生成可校验的 ARM64 完整离线交付包；必须在镜像构建和测试完成后执行。"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/offline"))
from release import create, digest, version_name


def shell(*args):
    return subprocess.check_output(args, text=True).strip()


def source_archive(destination: Path):
    allowed_dirs = ("swr_builder", "web", "deploy", "scripts", "tests", "docs")
    blocked = {"node_modules", "__pycache__", ".git", ".pytest_cache", "dist", "data"}
    with tarfile.open(destination, "w:gz") as tar:
        paths = [ROOT / name for name in ("README.md", "CHANGELOG.md", "Dockerfile", "config.example.yaml",
                                         "requirements.txt", "requirements-dev.txt", ".gitignore", ".dockerignore")]
        for name in allowed_dirs:
            paths.extend(p for p in (ROOT / name).rglob("*") if p.is_file())
        for path in sorted(paths):
            relative = path.relative_to(ROOT)
            if any(part in blocked for part in relative.parts):
                continue
            if path.is_symlink() or path.name.endswith((".pyc", ".env")) or path.name in (".env", ".npmrc", ".pypirc") or path.name.startswith(".env."):
                continue
            if path.name == "config.yaml" and str(relative) != "deploy/offline/config.yaml":
                continue
            tar.add(path, arcname=str(relative), recursive=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, default=Path("/data/swr/dist"))
    args = parser.parse_args()
    version_name(args.version)
    image = json.loads(shell("docker", "image", "inspect", args.image))[0]
    if image["Architecture"] != "arm64" or image["Os"] != "linux":
        raise SystemExit("镜像必须是 linux/arm64")
    contract = digest(ROOT / "deploy/offline/runtime-lock.json")
    for relative, expected in json.loads((ROOT / "deploy/offline/runtime-lock.json").read_text()).items():
        if digest(ROOT / relative) != expected:
            raise SystemExit("运行环境标识过期，请运行 scripts/runtime-contract.py 并重建镜像")
    runtime_contract = shell("docker", "run", "--rm", "--platform", "linux/arm64", "--network", "none",
                             "--entrypoint", "cat", args.image, "/opt/swr-runtime/contract")
    if contract != runtime_contract:
        raise SystemExit("镜像与当前依赖锁不同，请重建镜像")
    folder = args.output / f"swr-builder-arm64-{args.version}"
    if folder.exists():
        raise SystemExit(f"交付目录已存在，拒绝覆盖：{folder}")
    folder.mkdir(parents=True)
    for name in ("images", "releases", "ops", "tools", "dependencies"):
        (folder / name).mkdir()
    for name in ("install.sh", "upgrade.sh", "swrctl", "config.yaml", "swr-builder.env.example"):
        shutil.copy2(ROOT / "deploy/offline" / name, folder / name)
    for name in ("manage.py", "release.py", "check_dependencies.py"):
        shutil.copy2(ROOT / "deploy/offline" / name, folder / "ops" / name)
    for name in ("requirements.in", "requirements.lock", "runtime-lock.json"):
        shutil.copy2(ROOT / "deploy/offline" / name, folder / "dependencies" / name)
    shutil.copy2(ROOT / "requirements.txt", folder / "dependencies/requirements.base.txt")
    dependency_input = folder / "dependencies/requirements.in"
    dependency_input.write_text(dependency_input.read_text().replace("../../requirements.txt", "requirements.base.txt"))
    shutil.copytree(ROOT / "artifacts/arm64/wheelhouse", folder / "dependencies/wheelhouse")
    shutil.copy2(ROOT / "deploy/offline/install-docker.sh", folder / "tools/install-docker.sh")
    shutil.copy2(ROOT / "artifacts/arm64/vendor/docker-28.5.2-aarch64.tgz", folder / "tools")
    shutil.copy2(ROOT / "docs/ARM64离线部署与更新.md", folder / "部署文档.md")
    for path in (folder / "install.sh", folder / "upgrade.sh", folder / "swrctl", folder / "tools/install-docker.sh"):
        path.chmod(0o755)
    (folder / "runtime-image").write_text(args.image + "\n")
    create(ROOT, folder / "releases", args.version, contract)
    validation_path = ROOT / "artifacts/arm64/validation.json"
    if validation_path.exists():
        validation = json.loads(validation_path.read_text())
        with tarfile.open(next((folder / "releases").glob("*.tar.gz")), "r:gz") as release_tar:
            code = json.load(release_tar.extractfile("release.json"))
        import hashlib
        code_hash = hashlib.sha256(json.dumps(code["files"], sort_keys=True).encode()).hexdigest()
        operations_match = all(digest(ROOT / path) == sha for path, sha in validation.get("operations", {}).items())
        if (validation.get("result") == "passed" and validation.get("runtime_contract") == contract
                and validation.get("code_files_sha256") == code_hash and operations_match):
            shutil.copy2(validation_path, folder / "verification.json")
            record = ROOT / "docs/ARM64交付验收记录.md"
            if record.exists():
                shutil.copy2(record, folder / "验收记录.md")
    source_archive(folder / "source.tar.gz")
    report = {
        "schema": 1, "release": args.version, "architecture": "linux/arm64",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "image_tag": args.image, "image_id": image["Id"], "image_reported_bytes": image["Size"],
        "runtime_contract": contract, "python_wheel_count": len(list((folder / "dependencies/wheelhouse").glob("*.whl"))),
        "frontend_lock_sha256": digest(ROOT / "web/package-lock.json"),
        "default_root": "/data/swr", "default_port": 8082,
        "source_git_head": shell("git", "-C", str(ROOT), "rev-parse", "HEAD"),
        "source_includes_worktree_changes": True,
        "base_images": json.loads((ROOT / "deploy/offline/base-images.json").read_text()),
    }
    (folder / "manifest.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    # docker save 导出全部镜像层；不使用 export（export 会丢镜像元数据）。
    with (folder / "images/swr-builder-arm64.tar.gz").open("wb") as output:
        save = subprocess.Popen(["docker", "image", "save", "--platform", "linux/arm64", args.image], stdout=subprocess.PIPE)
        compress = subprocess.Popen(["gzip", "-1"], stdin=save.stdout, stdout=output)
        save.stdout.close()
        if compress.wait() or save.wait():
            raise SystemExit("镜像导出失败")
    (folder / "SHA256SUMS").write_text("".join(
        f"{digest(path)}  {path.relative_to(folder).as_posix()}\n"
        for path in sorted(folder.rglob("*")) if path.is_file() and path.name != "SHA256SUMS"))
    final = folder.parent / f"{folder.name}.tar.gz"
    with tarfile.open(final, "w:gz", compresslevel=1) as tar:
        tar.add(folder, arcname=folder.name)
    final.with_suffix(final.suffix + ".sha256").write_text(f"{digest(final)}  {final.name}\n")
    print(json.dumps({"bundle": str(final), "bytes": final.stat().st_size,
                      "sha256": digest(final), "image": image["Id"]}, indent=2))


if __name__ == "__main__":
    main()
