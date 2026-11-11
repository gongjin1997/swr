"""无第三方依赖的发布包创建、完整性验证和安全解包。"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def version_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", value):
        raise ValueError("版本名只能包含字母、数字、点、下划线和短横线，最长 80 字符")
    return value


def extract(archive: Path, destination: Path) -> None:
    """只接受普通文件与目录；拒绝链接、设备、重复路径和路径穿越。"""
    with tarfile.open(archive, "r:gz") as tar:
        names = set()
        members = tar.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise ValueError("包中包含非法路径")
            if not (member.isfile() or member.isdir()) or path.as_posix() in names:
                raise ValueError("包中包含链接、特殊文件或重复路径")
            names.add(path.as_posix())
        # Python 3.11 不依赖较新发行版的 extraction filter。
        for member in members:
            target = destination / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.isdir():
                target.mkdir(exist_ok=True)
            else:
                source = tar.extractfile(member)
                if source is None:
                    raise ValueError("无法读取包内文件")
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)


def verify(directory: Path, contract: str) -> dict:
    manifest = json.loads((directory / "release.json").read_text())
    version_name(manifest["version"])
    if manifest.get("schema") != 1 or manifest.get("architecture") != "arm64":
        raise ValueError("不是受支持的 ARM64 代码包")
    if manifest.get("runtime_contract") != contract:
        raise ValueError("运行依赖版本不匹配，需先使用对应的完整镜像包")
    actual = {}
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise ValueError("代码包不能包含符号链接")
        if path.is_file() and path != directory / "release.json":
            actual[path.relative_to(directory).as_posix()] = digest(path)
    if actual != manifest.get("files"):
        raise ValueError("代码文件缺失、多出或 SHA256 校验失败")
    if "swr_builder/__main__.py" not in actual or "swr_builder/webui/index.html" not in actual:
        raise ValueError("代码包缺少后端入口或前端编译产物")
    return manifest


def create(source: Path, output: Path, version: str, contract: str) -> Path:
    version_name(version)
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"swr-code-{version}.tar.gz"
    if archive.exists():
        raise ValueError(f"输出已存在：{archive}；请使用新的版本名")
    with tempfile.TemporaryDirectory(prefix="swr-release-") as temporary:
        payload = Path(temporary)
        shutil.copytree(source / "swr_builder", payload / "swr_builder",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
        manifest = {
            "schema": 1, "version": version, "architecture": "arm64",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "runtime_contract": contract,
            "files": {p.relative_to(payload).as_posix(): digest(p)
                      for p in sorted(payload.rglob("*")) if p.is_file()},
        }
        (payload / "release.json").write_text(json.dumps(manifest, indent=2) + "\n")
        verify(payload, contract)
        with tarfile.open(archive, "w:gz") as tar:
            for path in sorted(payload.rglob("*")):
                if path.is_file():
                    info = tar.gettarinfo(str(path), path.relative_to(payload).as_posix())
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    with path.open("rb") as stream:
                        tar.addfile(info, stream)
    archive.with_suffix(archive.suffix + ".sha256").write_text(f"{digest(archive)}  {archive.name}\n")
    return archive
