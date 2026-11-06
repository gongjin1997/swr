"""镜像地址与 Dockerfile FROM 解析的共用函数。"""
from __future__ import annotations

from typing import Iterable, List, Optional, Tuple


def registry_path_parts(reference: object) -> Optional[Tuple[str, str]]:
    """粗略拆出镜像地址中的 (组织, 仓库路径)，用于从历史应用数据推导项目授权。

    与 v1.2 迁移中的写法保持一致（迁移结果必须可复现），精确解析请用 registry.parse_image_ref。
    """
    if not isinstance(reference, str) or "/" not in reference:
        return None
    image_name = reference.split("@", 1)[0].split(":", 1)[0]
    parts = image_name.split("/")
    if len(parts) >= 3 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        parts = parts[1:]
    if len(parts) >= 2 and parts[0]:
        return parts[0], "/".join(parts[1:])
    return None


def dockerfile_from_images(templates: Iterable[str]) -> List[Optional[str]]:
    """返回模板中每条 FROM 指令引用的镜像（跳过 --platform 等参数）；FROM 后缺少镜像时对应项为 None。"""
    images: List[Optional[str]] = []
    for template in templates:
        for line in (template or "").splitlines():
            fields = line.strip().split()
            if not fields or fields[0].upper() != "FROM":
                continue
            index = 1
            while index < len(fields) and fields[index].startswith("--"):
                index += 1
            images.append(fields[index] if index < len(fields) else None)
    return images
