#!/usr/bin/env python3
"""在开发 / 构建机上制作补丁包（不含镜像）：完整代码（含编译后的前端）和 / 或 SQL 脚本。

    # 代码 + SQL，只允许应用到 2.0.0
    python3 scripts/build-arm64-patch.py 2.0.1 --sql patches/2.0.1/sql --base 2.0.0 --description "修复……"
    # 只有 SQL（代码保持现场当前版本）
    python3 scripts/build-arm64-patch.py 2.0.0-sql1 --no-code --sql patches/2.0.0-sql1/sql

前端是架构无关的静态文件、后端是纯 Python，所以补丁不需要 ARM64 构建机，只需要 Node 22 与 Python 3.11。
运行依赖标识取自 deploy/offline/runtime-lock.json，必须与现场完整包一致（完整包 manifest.json 的
runtime_contract，可用 --bundle-manifest 核对）；依赖变化后补丁会被现场拒绝，需要新的完整包。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/offline"))
from release import create_patch, digest  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("version", help="补丁版本号，例如 2.0.1；不能与已发布的版本重复")
    parser.add_argument("--sql", type=Path, help="SQL 目录（ddl/、dml/、*.sql）")
    parser.add_argument("--no-code", action="store_true", help="只含 SQL，不打入代码")
    parser.add_argument("--base", default="", help="只允许应用到这个版本（现场 swrctl status 中的 version）")
    parser.add_argument("--description", default="", help="一句话说明，记录在 patch.json 和现场的补丁记录中")
    parser.add_argument("--readme", type=Path, help="给现场运维看的说明（打包为 README.md）")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/patches")
    parser.add_argument("--bundle-manifest", type=Path, help="现场完整包的 manifest.json，核对运行依赖标识")
    parser.add_argument("--skip-web-build", action="store_true",
                        help="swr_builder/webui 已是当前源码的构建结果时，跳过 npm ci / npm run build")
    args = parser.parse_args()
    lock = json.loads((ROOT / "deploy/offline/runtime-lock.json").read_text())
    for relative, expected in lock.items():
        if digest(ROOT / relative) != expected:
            raise SystemExit(f"{relative} 与 runtime-lock.json 不一致：运行依赖变了，补丁无法用于现有完整包，"
                             "请制作新的完整包（scripts/build-arm64-bundle.sh）")
    contract = digest(ROOT / "deploy/offline/runtime-lock.json")
    if args.bundle_manifest:
        expected = json.loads(args.bundle_manifest.read_text())["runtime_contract"]
        if expected != contract:
            raise SystemExit(f"运行依赖标识 {contract[:12]} 与现场完整包的 {expected[:12]} 不同；"
                             "请检出制作该完整包时的依赖锁文件，或制作新的完整包")
    if not args.no_code:
        if not args.skip_web_build:
            subprocess.run(["npm", "--prefix", "web", "ci", "--no-audit", "--no-fund"], cwd=ROOT, check=True)
            subprocess.run(["npm", "--prefix", "web", "run", "build"], cwd=ROOT, check=True)
        if not (ROOT / "swr_builder/webui/index.html").is_file():
            raise SystemExit("缺少前端构建结果 swr_builder/webui/index.html")
        found = re.search(r'__version__ = "([^"]+)"', (ROOT / "swr_builder/__init__.py").read_text())
        if found and found.group(1) != args.version:
            print(f"提示：swr_builder/__init__.py 的版本是 {found.group(1)}，页面上显示的是它，"
                  f"与补丁版本 {args.version} 不同", file=sys.stderr)
    archive = create_patch(ROOT, args.output, args.version, contract, code=not args.no_code,
                           sql=args.sql.resolve() if args.sql else None, base=args.base,
                           description=args.description, readme=args.readme)
    with tarfile.open(archive, "r:gz") as tar:
        manifest = json.load(tar.extractfile("patch.json"))
    print(json.dumps({"patch": str(archive), "sha256": digest(archive), "runtime_contract": contract,
                      "code": manifest["code"], "sql": manifest["sql"], "base": manifest["base"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
