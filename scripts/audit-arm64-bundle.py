#!/usr/bin/env python3
"""独立核验交付目录中的哈希、完整镜像、代码、前端和离线依赖。"""
import argparse
import hashlib
import json
import re
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy/offline"))
from release import digest, extract, verify

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
for required in ("install.sh", "upgrade.sh", "swrctl", "source.tar.gz", "部署文档.md",
                 "ops/manage.py", "ops/release.py", "dependencies/requirements.lock",
                 "dependencies/runtime-lock.json", "tools/docker-28.5.2-aarch64.tgz"):
    assert (bundle / required).is_file(), required
manifest = json.loads((bundle / "manifest.json").read_text())
contract = digest(bundle / "dependencies/runtime-lock.json")
assert contract == manifest["runtime_contract"]
with tarfile.open(bundle / "images/swr-builder-arm64.tar.gz", "r:gz") as image:
    images = json.load(image.extractfile("manifest.json"))
    assert len(images) == 1, "交付包应只有一个目标平台镜像"
    description = images[0]
    assert (bundle / "runtime-image").read_text().strip() in description["RepoTags"]
    config_bytes = image.extractfile(description["Config"]).read()
    config = json.loads(config_bytes)
    assert config["architecture"] == "arm64" and config["os"] == "linux"
    config_digest = hashlib.sha256(config_bytes).hexdigest()
    names = {m.name for m in image.getmembers()}
    assert all(layer in names for layer in description["Layers"]), "镜像缺少基础层"
    assert "Healthcheck" in config["config"] and "Cmd" in config["config"]
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
with tarfile.open(bundle / "source.tar.gz", "r:gz") as source:
    names = {m.name for m in source.getmembers() if m.isfile()}
    for required in ("swr_builder/__main__.py", "web/package-lock.json", "deploy/offline/runtime-lock.json",
                     "scripts/build-arm64-bundle.sh", "config.example.yaml"):
        assert required in names, required
    assert not any(n.endswith((".env", "builder.db", "secret.key")) for n in names)
    locks = json.load(source.extractfile("deploy/offline/runtime-lock.json"))
    for name, sha in locks.items():
        assert hashlib.sha256(source.extractfile(name).read()).hexdigest() == sha, name
wheel_count = 0
for wheel in (bundle / "dependencies/wheelhouse").glob("*.whl"):
    assert "aarch64" in wheel.name or wheel.name.endswith("-any.whl"), wheel.name
    wheel_count += 1
assert wheel_count == manifest["python_wheel_count"]
print(json.dumps({"result": "passed", "checked_files": len(actual), "architecture": "linux/arm64",
                  "image_config_sha256": config_digest, "image_layers": len(description["Layers"]),
                  "code_files_sha256": code_digest, "python_wheels": wheel_count,
                  "runtime_contract": contract}, ensure_ascii=False, indent=2))
