#!/usr/bin/env python3
"""从实际 ARM64 wheel 制作完全固定并带哈希的依赖清单。"""
import hashlib
from pathlib import Path

root = Path(__file__).resolve().parents[1]
wheels = sorted((root / "artifacts/arm64/wheelhouse").glob("*.whl"))
if not wheels:
    raise SystemExit("没有 wheel；先运行 ARM64 pip download")
lines = ["# CPython 3.11 / Linux ARM64，所有直接与间接依赖都固定到包内 wheel。"]
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
(root / "deploy/offline/requirements.lock").write_text("\n".join(lines) + "\n")
print(f"已锁定 {len(wheels)} 个 ARM64 / 纯 Python wheel")
