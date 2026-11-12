#!/usr/bin/env python3
"""从实际 ARM64 wheel 制作完全固定并带哈希的依赖清单 deploy/offline/requirements.lock。

清单第二行记录 requirements.in 与 requirements.txt 的摘要；`--check` 只检查清单是否跟得上这两个文件
（不一致时 build-arm64-bundle.sh 会重新解析依赖，例如 v2 新增 psycopg 之后的第一次构建）。
"""
import hashlib
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
lock = root / "deploy/offline/requirements.lock"
INPUTS = ("deploy/offline/requirements.in", "requirements.txt")


def inputs_digest() -> str:
    value = hashlib.sha256()
    for name in INPUTS:
        value.update(name.encode() + b"\0" + (root / name).read_bytes() + b"\0")
    return value.hexdigest()


header = f"# inputs-sha256: {inputs_digest()}"
if "--check" in sys.argv[1:]:
    current = lock.read_text().splitlines()[:3] if lock.exists() else []
    if header not in current:
        print("requirements.lock 与 requirements.in / requirements.txt 不一致，需要在联网构建机重新解析", file=sys.stderr)
        sys.exit(1)
    print("requirements.lock 与依赖清单一致")
    sys.exit(0)

wheels = sorted((root / "artifacts/arm64/wheelhouse").glob("*.whl"))
if not wheels:
    raise SystemExit("没有 wheel；先运行 ARM64 pip download")
lines = ["# CPython 3.11 / Linux ARM64，所有直接与间接依赖都固定到包内 wheel。", header]
seen = set()
for wheel in wheels:
    name, version, *_ = wheel.name.split("-")
    name = name.replace("_", "-")
    if name.lower() in seen:
        raise SystemExit(f"重复版本：{name}；请在干净的 wheelhouse 中重新解析")
    seen.add(name.lower())
    with wheel.open("rb") as stream:
        sha = hashlib.file_digest(stream, "sha256").hexdigest()
    lines.append(f"{name}=={version} --hash=sha256:{sha}")
lock.write_text("\n".join(lines) + "\n")
print(f"已锁定 {len(wheels)} 个 ARM64 / 纯 Python wheel")
