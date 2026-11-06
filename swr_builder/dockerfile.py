"""Dockerfile 模板渲染、上传文件规划、导入已有 Dockerfile。"""
from __future__ import annotations

import json
import posixpath
import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

PLACEHOLDERS = ("BASE_IMAGE", "COPY_FILES", "TARGET_DIR", "NEW_TAG", "PREV_TAG", "IMAGE")
_PH_RE = re.compile(r"\{\{\s*([A-Za-z_]+)\s*\}\}")
_ARTIFACT_EXT = (".jar", ".war", ".ear", ".zip", ".tar", ".tar.gz", ".tgz")
_REPO_NAME = re.compile(r"^[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*$")

DEFAULT_INCREMENTAL_TEMPLATE = """\
# 累加构建：在上一版本镜像上叠加本次上传的文件（WORKDIR/EXPOSE/CMD 等沿用上一版本）
FROM {{BASE_IMAGE}}
{{COPY_FILES}}
"""

DEFAULT_FULL_TEMPLATE = """\
FROM {{BASE_IMAGE}}
WORKDIR {{TARGET_DIR}}
{{COPY_FILES}}
"""


def template_placeholders(template: str) -> List[str]:
    return [m.group(1).upper() for m in _PH_RE.finditer(template)]


def render_template(template: str, variables: Dict[str, str]) -> str:
    def repl(match: "re.Match[str]") -> str:
        key = match.group(1).upper()
        if key not in variables:
            raise ValueError(f"模板中存在未知变量 {{{{{key}}}}}")
        return variables[key]

    text = _PH_RE.sub(repl, template.replace("\r\n", "\n"))
    return text if text.endswith("\n") else text + "\n"


def copy_instruction(chown: str, target_dir: str) -> str:
    """把构建上下文 files/ 目录整体复制到目标目录，只产生一层。"""
    dest = target_dir.rstrip("/") + "/"
    flag = f"--chown={chown} " if chown else ""
    return f"COPY {flag}" + json.dumps(["files/", dest], ensure_ascii=False)


def render_dockerfile(app: Dict[str, Any], mode: str, base_image: str, new_tag: str,
                      prev_tag: str = "", image: str = "") -> str:
    template = app["incremental_template"] if mode == "incremental" else app["full_template"]
    variables = {
        "BASE_IMAGE": base_image,
        "COPY_FILES": copy_instruction(app.get("chown", ""), app.get("target_dir") or "/app"),
        "TARGET_DIR": app.get("target_dir") or "/app",
        "NEW_TAG": new_tag,
        "PREV_TAG": prev_tag or "",
        "IMAGE": image,
    }
    return render_template(template, variables)


# ---------------------------------------------------------------------------- 上传文件
def safe_filename(name: str) -> str:
    base = (name or "").replace("\\", "/").split("/")[-1].strip()
    if not base or base in (".", "..") or len(base) > 255:
        raise ValueError(f"文件名不合法：{name!r}")
    if any(ord(ch) < 32 for ch in base) or '"' in base:
        raise ValueError(f"文件名包含特殊字符：{name!r}")
    return base


def plan_files(target_dir: str, target_name: str, filenames: List[str], extract_zip: bool) -> List[Dict[str, Any]]:
    """计算每个上传文件在构建上下文 files/ 下的路径，以及最终在镜像里的位置。"""
    target_dir = target_dir or "/app"
    names = [safe_filename(n) for n in filenames]
    if not names:
        raise ValueError("请至少上传一个文件")
    if len(set(names)) != len(names):
        raise ValueError("上传的文件中有重名文件")
    single = len(names) == 1
    entries = []
    for name in names:
        if extract_zip and name.lower().endswith(".zip"):
            entries.append({"name": name, "path": "", "dest": target_dir.rstrip("/") + "/", "extract": True})
        elif single and target_name:
            entries.append({"name": name, "path": target_name,
                            "dest": posixpath.join(target_dir, target_name), "extract": False})
        else:
            entries.append({"name": name, "path": name, "dest": posixpath.join(target_dir, name), "extract": False})
    return entries


def _zip_member_name(info: zipfile.ZipInfo) -> str:
    name = info.filename
    if not info.flag_bits & 0x800:  # 非 UTF-8 编码的文件名（Windows 压缩工具常见 GBK）
        try:
            name = name.encode("cp437").decode("gbk")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return name.replace("\\", "/")


def extract_zip_safe(zip_path: Path, dest_dir: Path, max_bytes: int) -> List[str]:
    """安全解压：拒绝 ../ 和绝对路径（zip slip），并限制解压后的总大小（zip 炸弹）。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    extracted: List[str] = []
    try:
        archive = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise ValueError(f"{zip_path.name} 不是有效的 zip 文件") from exc
    with archive:
        members = archive.infolist()
        if sum(m.file_size for m in members) > max_bytes:
            raise ValueError("zip 解压后体积过大")
        for info in members:
            name = _zip_member_name(info)
            parts = PurePosixPath(name).parts
            if name.startswith("/") or ".." in parts or (parts and ":" in parts[0]):
                raise ValueError(f"压缩包中包含不安全的路径：{name}")
            target = dest_dir.joinpath(*parts) if parts else dest_dir
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            extracted.append("/".join(parts))
    if not extracted:
        raise ValueError(f"{zip_path.name} 是空压缩包")
    return extracted


# ---------------------------------------------------------------------------- 导入 Dockerfile
def _parse_copy(rest: str) -> Optional[Dict[str, Any]]:
    flags: Dict[str, str] = {}
    text = rest.strip()
    while text.startswith("--"):
        match = re.match(r"--([A-Za-z-]+)(?:=(\S+))?\s*", text)
        if not match:
            break
        flags[match.group(1).lower()] = match.group(2) or ""
        text = text[match.end():]
    if text.startswith("["):
        try:
            items = json.loads(text)
        except ValueError:
            return None
        if not isinstance(items, list) or len(items) < 2:
            return None
        items = [str(i) for i in items]
    else:
        items = text.split()
        if len(items) < 2:
            return None
    return {"flags": flags, "sources": items[:-1], "dest": items[-1]}


def _logical_lines(text: str) -> List[Tuple[str, List[str]]]:
    """把以 \\ 续行的多行指令合并成一条逻辑行，返回 (合并后的指令, 原始行列表)。"""
    result: List[Tuple[str, List[str]]] = []
    buf_raw: List[str] = []
    buf_text: List[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.rstrip()
        if buf_raw:
            buf_raw.append(line)
            if line.strip().startswith("#"):
                continue  # 续行中的注释行会被 Docker 忽略
            if line.endswith("\\"):
                buf_text.append(line[:-1].strip())
                continue
            buf_text.append(line.strip())
            result.append((" ".join(t for t in buf_text if t), buf_raw))
            buf_raw, buf_text = [], []
            continue
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and line.endswith("\\"):
            buf_raw, buf_text = [line], [line[:-1].strip()]
            continue
        result.append((stripped, [line]))
    if buf_raw:
        result.append((" ".join(t for t in buf_text if t), buf_raw))
    return result


def import_dockerfile(text: str) -> Dict[str, Any]:
    """从已有 Dockerfile 识别基础镜像、jar 的目标路径和属主，并生成全量构建模板。"""
    out: List[str] = []
    warnings: List[str] = []
    base_image = ""
    workdir = "/"
    target_dir, target_name, chown = "", "", ""
    from_count = 0
    copy_done = False
    for stripped, raws in _logical_lines(text):
        if not stripped or stripped.startswith("#"):
            out.extend(raws)
            continue
        inst, _, rest = stripped.partition(" ")
        inst = inst.upper()
        rest = rest.strip()
        if inst == "FROM":
            from_count += 1
            tokens = rest.split()
            flags = [t for t in tokens if t.startswith("--")]
            args = [t for t in tokens if not t.startswith("--")]
            if from_count == 1 and args:
                base_image = args[0]
                prefix = " ".join(flags) + " " if flags else ""
                suffix = " " + " ".join(args[1:]) if len(args) > 1 else ""
                out.append(f"FROM {prefix}{{{{BASE_IMAGE}}}}{suffix}")
                continue
        elif inst == "WORKDIR" and rest:
            workdir = posixpath.normpath(posixpath.join(workdir, rest.strip('"')))
        elif inst in ("COPY", "ADD") and not copy_done:
            parsed = _parse_copy(rest)
            if parsed and "from" not in parsed["flags"]:
                sources, dest = parsed["sources"], parsed["dest"]
                if any(s.lower().endswith(_ARTIFACT_EXT) for s in sources):
                    is_dir = dest.endswith("/") or dest in (".", "./") or len(sources) > 1
                    abs_dest = posixpath.normpath(dest if dest.startswith("/") else posixpath.join(workdir, dest))
                    if is_dir:
                        target_dir = abs_dest
                        src = sources[0]
                        target_name = posixpath.basename(src) if len(sources) == 1 and "*" not in src else ""
                    else:
                        target_dir = posixpath.dirname(abs_dest) or "/"
                        target_name = posixpath.basename(abs_dest)
                    chown = parsed["flags"].get("chown", "")
                    copy_done = True
                    out.append("{{COPY_FILES}}")
                    continue
        out.extend(raws)

    while out and not out[-1].strip():
        out.pop()
    if from_count == 0:
        warnings.append("没有找到 FROM 指令，已在开头补上 FROM {{BASE_IMAGE}}")
        out.insert(0, "FROM {{BASE_IMAGE}}")
    elif from_count > 1:
        warnings.append("检测到多阶段构建（多个 FROM），只替换了第一个 FROM，请检查模板")
    if not copy_done:
        warnings.append("没有识别到复制 jar/war/zip 的 COPY 指令，已把 {{COPY_FILES}} 放在 CMD/ENTRYPOINT 之前，请检查")
        index = next((i for i, l in enumerate(out)
                      if l.strip().upper().startswith(("CMD", "ENTRYPOINT"))), len(out))
        out.insert(index, "{{COPY_FILES}}")
        target_dir = target_dir or (workdir if workdir != "/" else "/app")
    lowered = base_image.lower()
    if "arm64" in lowered or "aarch64" in lowered:
        warnings.append("基础镜像是 arm64 架构：如果构建机是 x86，请把“构建平台”设为 linux/arm64")
    repo_guess = ""
    if target_name:
        stem = re.sub(r"\.(jar|war|ear|zip|tar\.gz|tgz|tar)$", "", target_name.lower())
        if _REPO_NAME.match(stem):
            repo_guess = stem
    return {
        "base_image": base_image,
        "target_dir": target_dir or "/app",
        "target_name": target_name,
        "chown": chown,
        "full_template": "\n".join(out) + "\n",
        "repository": repo_guess,
        "name": repo_guess,
        "warnings": warnings,
    }
