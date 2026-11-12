"""无第三方依赖的代码包 / 补丁包创建、完整性验证和安全解包。

代码包 swr-code-<版本>.tar.gz：release.json（逐文件 SHA256、运行依赖标识）+ 完整的 swr_builder/（含编译后的前端）。

补丁包 swr-patch-<版本>.tar.gz：不含镜像，用于在已安装的环境上更新代码和数据库。
    patch.json        补丁说明与逐文件 SHA256
    README.md         可选，给现场运维看的说明
    code/             可选，一个完整的代码包（release.json + swr_builder/），版本号与补丁相同
    sql/ddl/*.sql     可选，结构变更（建表、加列、索引……）
    sql/dml/*.sql     可选，数据变更（铺底数据、数据修正……）
    sql/*.sql         可选，在 ddl/、dml/ 之后执行
代码与 SQL 至少有一项。SQL 的执行规则见 swr_builder/store/sqlscripts.py。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Optional

VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
SQL_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.sql")
SQL_FOLDERS = ("ddl", "dml")
PATCH_MANIFEST = "patch.json"
CODE_MANIFEST = "release.json"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def version_name(value: str) -> str:
    if not isinstance(value, str) or not VERSION_RE.fullmatch(value):
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


def _files(directory: Path, manifest_name: str) -> dict:
    actual = {}
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise ValueError("包内不能有符号链接")
        if path.is_file() and path != directory / manifest_name:
            actual[path.relative_to(directory).as_posix()] = digest(path)
    return actual


def verify(directory: Path, contract: str) -> dict:
    """校验解开的代码包；contract 是运行镜像的依赖标识，必须与代码包记录的一致。"""
    manifest = json.loads((directory / CODE_MANIFEST).read_text())
    version_name(manifest["version"])
    if manifest.get("schema") != 1 or manifest.get("architecture") != "arm64":
        raise ValueError("不是受支持的 ARM64 代码包")
    if manifest.get("runtime_contract") != contract:
        raise ValueError("运行依赖版本不匹配，需先使用对应的完整镜像包")
    actual = _files(directory, CODE_MANIFEST)
    if actual != manifest.get("files"):
        raise ValueError("代码文件缺失、多出或 SHA256 校验失败")
    if "swr_builder/__main__.py" not in actual or "swr_builder/webui/index.html" not in actual:
        raise ValueError("代码包缺少后端入口或前端编译产物")
    return manifest


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stage_code(source: Path, payload: Path, version: str, contract: str) -> dict:
    """把 source/swr_builder（前端须已编译到 swr_builder/webui）整理成代码包目录 payload，返回 release.json。"""
    version_name(version)
    shutil.copytree(source / "swr_builder", payload / "swr_builder",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
    manifest = {
        "schema": 1, "version": version, "architecture": "arm64",
        "created_at": _now(),
        "runtime_contract": contract,
        "files": {p.relative_to(payload).as_posix(): digest(p)
                  for p in sorted(payload.rglob("*")) if p.is_file()},
    }
    (payload / CODE_MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
    return verify(payload, contract)


def _write_archive(payload: Path, archive: Path) -> Path:
    """文件按路径排序、属主清零，同样的内容得到同样的文件列表；旁边写 .sha256。"""
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


def image_archive(path: Path) -> list:
    """读取 docker save 生成的镜像包（.tar / .tar.gz）：每个镜像的标签、平台、配置与层数；缺少镜像层时报错。"""
    with tarfile.open(path, "r:*") as tar:
        names = {member.name for member in tar.getmembers()}
        try:
            manifest = json.load(tar.extractfile("manifest.json"))
        except KeyError:
            raise ValueError(f"{path.name} 不是 docker save 生成的镜像包（缺少 manifest.json）") from None
        images = []
        for item in manifest:
            raw = tar.extractfile(item["Config"]).read()
            config = json.loads(raw)
            if any(layer not in names for layer in item["Layers"]):
                raise ValueError(f"{path.name} 缺少镜像层")
            images.append({"tags": item.get("RepoTags") or [], "architecture": config.get("architecture"),
                           "os": config.get("os"), "config": config,
                           "config_sha256": hashlib.sha256(raw).hexdigest(), "layers": len(item["Layers"])})
    return images


def create(source: Path, output: Path, version: str, contract: str) -> Path:
    version_name(version)
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"swr-code-{version}.tar.gz"
    if archive.exists():
        raise ValueError(f"输出已存在：{archive}；请使用新的版本名")
    with tempfile.TemporaryDirectory(prefix="swr-release-") as temporary:
        payload = Path(temporary)
        stage_code(source, payload, version, contract)
        return _write_archive(payload, archive)


# ---------------------------------------------------------------- 补丁包
def _sql_path(relative: str) -> bool:
    """sql/ 下允许的文件（relative 相对 sql/）：ddl/、dml/ 或根目录下的 *.sql。"""
    parts = PurePosixPath(relative).parts
    if len(parts) == 1:
        return bool(SQL_NAME_RE.fullmatch(parts[0]))
    return len(parts) == 2 and parts[0] in SQL_FOLDERS and bool(SQL_NAME_RE.fullmatch(parts[1]))


def sql_order(paths) -> list:
    """与 sqlscripts.discover 相同的执行顺序：ddl/ → dml/ → 根目录，同一目录内按文件名排序。"""
    def key(path: str):
        parts = PurePosixPath(path).parts
        folder = parts[0] if len(parts) == 2 else ""
        return ({"ddl": 0, "dml": 1, "": 2}[folder], parts[-1])
    return sorted(paths, key=key)


def verify_patch(directory: Path) -> dict:
    """校验解开的补丁包（文件清单、SHA256、目录约定、内含代码包的完整性），返回 patch.json。

    代码所需的运行镜像是否已安装由调用方（部署控制器）根据 runtime_contract 判断。
    """
    path = directory / PATCH_MANIFEST
    if not path.is_file():
        raise ValueError("不是补丁包：缺少 patch.json")
    manifest = json.loads(path.read_text())
    if (manifest.get("schema") != 1 or manifest.get("kind") != "patch"
            or manifest.get("architecture") != "arm64"):
        raise ValueError("不是受支持的 ARM64 补丁包")
    version = version_name(manifest.get("version", ""))
    if manifest.get("base"):
        version_name(manifest["base"])
    actual = _files(directory, PATCH_MANIFEST)
    if actual != manifest.get("files"):
        raise ValueError("补丁文件缺失、多出或 SHA256 校验失败")
    sql = []
    for relative in actual:
        if relative == "README.md" or relative.startswith("code/"):
            continue
        if not (relative.startswith("sql/") and _sql_path(relative[4:])):
            raise ValueError(f"补丁包中不认识的文件 {relative}：只能有 README.md、code/ 与 sql/{{ddl,dml}}/*.sql")
        sql.append(relative[4:])
    if manifest.get("sql") != sql_order(sql):
        raise ValueError("patch.json 中的 SQL 清单与包内文件不一致")
    has_code = any(relative.startswith("code/") for relative in actual)
    if bool(manifest.get("code")) != has_code:
        raise ValueError("patch.json 的 code 标记与包内文件不一致")
    if has_code:
        code = verify(directory / "code", manifest.get("runtime_contract", ""))
        if code["version"] != version:
            raise ValueError(f"补丁 {version} 内的代码版本是 {code['version']}，两者必须相同")
    if not has_code and not sql:
        raise ValueError("补丁包既没有代码也没有 SQL")
    return manifest


def load_sqlscripts(source: Path):
    """按文件路径加载源码中的 swr_builder/store/sqlscripts.py（只依赖标准库），用于打包前校验 SQL。"""
    path = source / "swr_builder/store/sqlscripts.py"
    if not path.is_file():
        raise ValueError(f"源码中没有 {path}，无法校验 SQL 脚本")
    spec = importlib.util.spec_from_file_location("swr_offline_sqlscripts", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass 需要能在 sys.modules 中找到模块
    spec.loader.exec_module(module)
    return module


def create_patch(source: Path, output: Path, version: str, contract: str, *, code: bool = True,
                 sql: Optional[Path] = None, base: str = "", description: str = "",
                 readme: Optional[Path] = None) -> Path:
    """制作补丁包。code=True 时打入 source 的完整代码（前端须已编译）；sql 是含 ddl/、dml/ 的脚本目录。"""
    version_name(version)
    if base:
        version_name(base)
    if not code and sql is None:
        raise ValueError("补丁包至少要包含代码或 SQL")
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"swr-patch-{version}.tar.gz"
    if archive.exists():
        raise ValueError(f"输出已存在：{archive}；请使用新的版本名")
    scripts = []
    if sql is not None:
        if not sql.is_dir():
            raise ValueError(f"SQL 目录不存在：{sql}")
        # 与现场执行时相同的校验：文件名、编码、不能自带 BEGIN / COMMIT、不能有 psql 元命令
        scripts = load_sqlscripts(source).discover(sql)
        if not scripts:
            raise ValueError(f"SQL 目录 {sql} 中没有 .sql 脚本")
    with tempfile.TemporaryDirectory(prefix="swr-patch-") as temporary:
        payload = Path(temporary)
        if code:
            (payload / "code").mkdir()
            stage_code(source, payload / "code", version, contract)
        for script in scripts:
            target = payload / "sql" / script.path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(sql / script.path, target)
        if readme is not None:
            shutil.copyfile(readme, payload / "README.md")
        files = {p.relative_to(payload).as_posix(): digest(p) for p in sorted(payload.rglob("*")) if p.is_file()}
        manifest = {
            "schema": 1, "kind": "patch", "version": version, "architecture": "arm64",
            "created_at": _now(), "runtime_contract": contract, "base": base, "description": description,
            "code": code, "sql": sql_order(s.path for s in scripts), "files": files,
        }
        (payload / PATCH_MANIFEST).write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        verify_patch(payload)
        return _write_archive(payload, archive)
