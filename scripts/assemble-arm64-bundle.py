#!/usr/bin/env python3
"""生成可校验的 ARM64 全量离线交付包；在镜像构建和检查完成后执行（由 build-arm64-bundle.sh 调用）。

交付目录 swr-builder-arm64-<版本>/（同时打成 .tar.gz 并附 .sha256）：
    images/swr-builder-arm64.tar.gz    平台运行镜像（docker save）
    images/postgres-arm64.tar.gz       内置数据库 PostgreSQL 镜像
    releases/swr-code-<版本>.tar.gz    代码与编译后的前端
    seed/                              铺底资源：sql/（安装后执行一次）、images/（业务基础镜像）、certs/（CA 证书）
    install.sh / upgrade.sh / swrctl / ops/      安装、升级与日常管理
    config.yaml / swr-builder.env.example        配置模板
    tools/ dependencies/ source.tar.gz 部署文档.md runtime-image postgres-image manifest.json SHA256SUMS
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/offline"))
from release import create, digest, image_archive, load_sqlscripts, version_name  # noqa: E402

ROOT_FILES = ("README.md", "CHANGELOG.md", "AGENTS.md", "CLAUDE.md", "DESIGN.md", "Dockerfile", "config.example.yaml",
              "requirements.txt", "requirements-dev.txt", ".gitignore", ".dockerignore")


def shell(*args):
    return subprocess.check_output(args, text=True).strip()


def inspect(tag: str) -> dict:
    # containerd image store 下的多架构标签默认解析为 manifest index，不带架构字段。
    image = json.loads(shell("docker", "image", "inspect", "--platform", "linux/arm64", tag))[0]
    if image["Architecture"] != "arm64" or image["Os"] != "linux":
        raise SystemExit(f"镜像 {tag} 必须是 linux/arm64")
    return image


def save_image(tag: str, destination: Path) -> None:
    # docker save 导出全部镜像层；不使用 export（export 会丢镜像元数据）。
    with destination.open("wb") as output:
        save = subprocess.Popen(["docker", "image", "save", "--platform", "linux/arm64", tag], stdout=subprocess.PIPE)
        compress = subprocess.Popen(["gzip", "-1"], stdin=save.stdout, stdout=output)
        save.stdout.close()
        if compress.wait() or save.wait():
            raise SystemExit(f"镜像导出失败：{tag}")


def source_archive(destination: Path):
    allowed_dirs = ("swr_builder", "web", "deploy", "scripts", "tests", "docs")
    blocked = {"node_modules", "__pycache__", ".git", ".pytest_cache", "dist", "data", "pgdata"}
    with tarfile.open(destination, "w:gz") as tar:
        paths = [ROOT / name for name in ROOT_FILES if (ROOT / name).is_file()]
        for name in allowed_dirs:
            paths.extend(p for p in (ROOT / name).rglob("*") if p.is_file())
        for path in sorted(paths):
            relative = path.relative_to(ROOT)
            if any(part in blocked for part in relative.parts):
                continue
            if path.is_symlink() or path.name.endswith((".pyc", ".env")) or path.name in (".env", ".npmrc", ".pypirc") \
                    or path.name.startswith(".env."):
                continue
            if path.name == "config.yaml" and str(relative) != "deploy/offline/config.yaml":
                continue
            tar.add(path, arcname=str(relative), recursive=False)


def copy_seed(seed: Path | None, folder: Path) -> dict:
    """铺底资源：sql/ 用与现场相同的规则校验；images/ 必须是 linux/arm64 的 docker save 包；certs/ 只放普通文件。"""
    target = folder / "seed"
    target.mkdir()
    shutil.copy2(ROOT / "deploy/offline/seed/README.md", target / "README.md")
    listing: dict = {"sql": [], "images": [], "certs": []}
    if seed is None:
        return listing
    seed = seed.resolve()
    unknown = sorted(p.name for p in seed.iterdir() if p.name not in ("sql", "images", "certs", "README.md"))
    if unknown:
        raise SystemExit(f"铺底目录中不认识的内容 {unknown}：只支持 sql/、images/、certs/")
    if (seed / "sql").is_dir():
        for script in load_sqlscripts(ROOT).discover(seed / "sql"):
            destination = target / "sql" / script.path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(seed / "sql" / script.path, destination)
            listing["sql"].append(script.path)
    if (seed / "images").is_dir():
        for path in sorted((seed / "images").iterdir()):
            if not path.is_file() or path.is_symlink() or not path.name.endswith((".tar", ".tar.gz")):
                raise SystemExit(f"seed/images 只能放 docker save 生成的 .tar / .tar.gz：{path.name}")
            images = image_archive(path)
            for item in images:
                if (item["architecture"], item["os"]) != ("arm64", "linux"):
                    raise SystemExit(f"{path.name} 中的镜像 {item['tags']} 不是 linux/arm64")
            (target / "images").mkdir(exist_ok=True)
            shutil.copy2(path, target / "images" / path.name)
            listing["images"].append({"file": path.name, "tags": [tag for item in images for tag in item["tags"]]})
    if (seed / "certs").is_dir():
        for path in sorted((seed / "certs").iterdir()):
            if not path.is_file() or path.is_symlink():
                raise SystemExit(f"seed/certs 只能放证书文件：{path.name}")
            (target / "certs").mkdir(exist_ok=True)
            shutil.copy2(path, target / "certs" / path.name)
            listing["certs"].append(path.name)
    return listing


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", required=True, help="平台运行镜像标签")
    parser.add_argument("--postgres-image", required=True, help="PostgreSQL 镜像的本地标签")
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/dist")
    parser.add_argument("--seed", type=Path, help="铺底资源目录（sql/、images/、certs/）")
    args = parser.parse_args()
    version_name(args.version)
    image = inspect(args.image)
    postgres = inspect(args.postgres_image)
    major = next((item.split("=", 1)[1] for item in postgres["Config"].get("Env") or []
                  if item.startswith("PG_MAJOR=")), "")
    if not major:
        raise SystemExit("PostgreSQL 镜像缺少 PG_MAJOR，请使用官方 postgres 镜像")
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
    (folder / "postgres-image").write_text(args.postgres_image + "\n")
    seed = copy_seed(args.seed, folder)
    create(ROOT, folder / "releases", args.version, contract)
    validation_path = ROOT / "artifacts/arm64/validation.json"
    if validation_path.exists():
        validation = json.loads(validation_path.read_text())
        with tarfile.open(next((folder / "releases").glob("*.tar.gz")), "r:gz") as release_tar:
            code = json.load(release_tar.extractfile("release.json"))
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
        "schema": 2, "release": args.version, "architecture": "linux/arm64",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "image_tag": args.image, "image_id": image["Id"], "image_reported_bytes": image["Size"],
        "postgres_image_tag": args.postgres_image, "postgres_image_id": postgres["Id"], "postgres_major": major,
        "runtime_contract": contract,
        "python_wheel_count": len(list((folder / "dependencies/wheelhouse").glob("*.whl"))),
        "frontend_lock_sha256": digest(ROOT / "web/package-lock.json"),
        "default_root": "/data/swr", "default_port": 8082, "seed": seed,
        "source_git_head": shell("git", "-C", str(ROOT), "rev-parse", "HEAD"),
        "source_includes_worktree_changes": True,
        "base_images": json.loads((ROOT / "deploy/offline/base-images.json").read_text()),
    }
    (folder / "manifest.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    save_image(args.image, folder / "images/swr-builder-arm64.tar.gz")
    save_image(args.postgres_image, folder / "images/postgres-arm64.tar.gz")
    (folder / "SHA256SUMS").write_text("".join(
        f"{digest(path)}  {path.relative_to(folder).as_posix()}\n"
        for path in sorted(folder.rglob("*")) if path.is_file() and path.name != "SHA256SUMS"))
    final = folder.parent / f"{folder.name}.tar.gz"
    with tarfile.open(final, "w:gz", compresslevel=1) as tar:
        tar.add(folder, arcname=folder.name)
    final.with_suffix(final.suffix + ".sha256").write_text(f"{digest(final)}  {final.name}\n")
    print(json.dumps({"bundle": str(final), "bytes": final.stat().st_size, "sha256": digest(final),
                      "image": image["Id"], "postgres": postgres["Id"], "seed": seed}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
