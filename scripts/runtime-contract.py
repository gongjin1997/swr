#!/usr/bin/env python3
"""运行环境标识同时覆盖 Python、前端构建依赖和基础镜像。"""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
files = ["deploy/offline/requirements.lock", "web/package-lock.json", "deploy/offline/base-images.json"]
manifest = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files}
target = root / "deploy/offline/runtime-lock.json"
target.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
print(hashlib.sha256(target.read_bytes()).hexdigest())
