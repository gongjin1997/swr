"""上传保存与日志增量读取（构建日志、发布日志共用）。"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Tuple

CHUNK = 1024 * 1024


async def save_upload(upload: Any, dest: Path, limit: int) -> Tuple[int, str]:
    """把上传文件流式写到 dest，返回 (大小, sha256)。超过 limit 或空文件抛 ValueError。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with open(dest, "wb") as fh:
        while True:
            chunk = await upload.read(CHUNK)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise ValueError(f"文件 {upload.filename} 超过大小限制 {limit // 1024 // 1024} MB")
            digest.update(chunk)
            fh.write(chunk)
    if size == 0:
        raise ValueError(f"文件 {upload.filename} 是空文件")
    return size, digest.hexdigest()


def read_log(path: Path, offset: int, max_bytes: int = 256 * 1024) -> Tuple[str, int, int]:
    """从 offset 开始读取最多 max_bytes，截断到最后一个完整行。返回 (文本, 新 offset, 文件大小)。"""
    if not path.exists():
        return "", 0, 0
    size = path.stat().st_size
    offset = min(max(offset, 0), size)
    with open(path, "rb") as fh:
        fh.seek(offset)
        data = fh.read(max_bytes)
    cut = data.rfind(b"\n")
    if cut >= 0:
        data = data[:cut + 1]
    elif len(data) < max_bytes:
        data = b""
    return data.decode("utf-8", "replace"), offset + len(data), size
