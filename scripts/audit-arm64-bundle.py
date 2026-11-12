#!/usr/bin/env python3
"""独立核验交付目录：文件哈希、两个 ARM64 镜像、代码、前端、铺底资源和离线依赖。"""
import argparse
import hashlib
import json
import re
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy/offline"))
from release import digest, extract, image_archive, load_sqlscripts, verify  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("bundle", type=Path)
args = parser.parse_args()
bundle = args.bundle.resolve()
expected = {}
for line in (bundle / "SHA256SUMS").read_text().splitlines():
    sha, name = line.split("  ", 1)
    path = Path(name)
    assert not path.is_absolute() and ".." not in path.parts
    assert name not in expected
    expected[name] = sha
actual = {p.relative_to(bundle).as_posix(): digest(p) for p in bundle.rglob("*")
          if p.is_file() and p.name != "SHA256SUMS"}
assert expected == actual, "包内文件列表或 SHA256 不匹配"
for required in ("install.sh", "upgrade.sh", "swrctl", "source.tar.gz", "部署文档.md", "config.yaml",
                 "swr-builder.env.example", "runtime-image", "postgres-image", "seed/README.md",
                 "ops/manage.py", "ops/release.py", "ops/check_dependencies.py", "dependencies/requirements.lock",
                 "dependencies/runtime-lock.json", "tools/docker-28.5.2-aarch64.tgz", "tools/install-docker.sh",
                 "images/swr-builder-arm64.tar.gz", "images/postgres-arm64.tar.gz"):
    assert (bundle / required).is_file(), required
manifest = json.loads((bundle / "manifest.json").read_text())
assert manifest["schema"] == 2
contract = digest(bundle / "dependencies/runtime-lock.json")
assert contract == manifest["runtime_contract"]


def single_image(name, tag_file):
    images = image_archive(bundle / "images" / name)
    assert len(images) == 1, f"{name} 应只有一个目标平台镜像"
    image = images[0]
    assert (bundle / tag_file).read_text().strip() in image["tags"], f"{name} 的标签与 {tag_file} 不符"
    assert (image["architecture"], image["os"]) == ("arm64", "linux"), name
    return image


runtime = single_image("swr-builder-arm64.tar.gz", "runtime-image")
assert "Healthcheck" in runtime["config"]["config"] and "Cmd" in runtime["config"]["config"]
postgres = single_image("postgres-arm64.tar.gz", "postgres-image")
assert f"PG_MAJOR={manifest['postgres_major']}" in (postgres["config"]["config"].get("Env") or []), "PostgreSQL 主版本不符"
archives = list((bundle / "releases").glob("*.tar.gz"))
assert len(archives) == 1
with tempfile.TemporaryDirectory(prefix="swr-bundle-audit-") as temporary:
    payload = Path(temporary)
    extract(archives[0], payload)
    code = verify(payload, contract)
    assert code["version"] == manifest["release"]
    html = (payload / "swr_builder/webui/index.html").read_text()
    for asset in re.findall(r'(?:src|href)="(/assets/[^\"]+)"', html):
        assert (payload / "swr_builder/webui" / asset.lstrip("/")).is_file(), asset
    assert "OFFLINE QA" not in html
    assert "swr_offline_upgrade_probe" not in (payload / "swr_builder/__init__.py").read_text()
    code_digest = hashlib.sha256(json.dumps(code["files"], sort_keys=True).encode()).hexdigest()
    # 铺底 SQL 用包内代码的规则重新校验，清单与 manifest 一致
    seed = bundle / "seed"
    scripts = [s.path for s in load_sqlscripts(payload).discover(seed / "sql")] if (seed / "sql").is_dir() else []
    assert scripts == manifest["seed"]["sql"], "铺底 SQL 与 manifest 不一致"
listed_images = sorted(item["file"] for item in manifest["seed"]["images"])
found_images = sorted(p.name for p in (bundle / "seed/images").glob("*")) if (bundle / "seed/images").is_dir() else []
assert listed_images == found_images, "铺底镜像与 manifest 不一致"
for name in found_images:
    for image in image_archive(bundle / "seed/images" / name):
        assert (image["architecture"], image["os"]) == ("arm64", "linux"), name
certs = sorted(p.name for p in (bundle / "seed/certs").glob("*")) if (bundle / "seed/certs").is_dir() else []
assert certs == sorted(manifest["seed"]["certs"]), "铺底证书与 manifest 不一致"
with tarfile.open(bundle / "source.tar.gz", "r:gz") as source:
    names = {m.name for m in source.getmembers() if m.isfile()}
    for required in ("swr_builder/__main__.py", "web/package-lock.json", "deploy/offline/runtime-lock.json",
                     "deploy/offline/config.yaml", "scripts/build-arm64-bundle.sh", "scripts/build-arm64-patch.py",
                     "config.example.yaml"):
        assert required in names, required
    assert not any(n.endswith((".env", "builder.db", "secret.key")) for n in names)
    locks = json.load(source.extractfile("deploy/offline/runtime-lock.json"))
    for name, sha in locks.items():
        assert hashlib.sha256(source.extractfile(name).read()).hexdigest() == sha, name
wheel_count = 0
wheels = set()
for wheel in (bundle / "dependencies/wheelhouse").glob("*.whl"):
    assert "aarch64" in wheel.name or wheel.name.endswith("-any.whl"), wheel.name
    wheels.add(wheel.name.split("-")[0].lower())
    wheel_count += 1
assert wheel_count == manifest["python_wheel_count"]
assert {"psycopg", "psycopg_binary"} <= wheels, "缺少 PostgreSQL 驱动 psycopg[binary]，请重新解析依赖"
print(json.dumps({"result": "passed", "checked_files": len(actual), "architecture": "linux/arm64",
                  "image_config_sha256": runtime["config_sha256"], "image_layers": runtime["layers"],
                  "postgres_config_sha256": postgres["config_sha256"], "postgres_major": manifest["postgres_major"],
                  "code_files_sha256": code_digest, "python_wheels": wheel_count, "seed": manifest["seed"],
                  "runtime_contract": contract}, ensure_ascii=False, indent=2))
