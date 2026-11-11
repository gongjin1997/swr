"""请求体字段校验的小工具。校验失败抛 ValueError，由全局处理器转换为 400。"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable

DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?")


def text_field(data: Dict[str, Any], key: str, limit: int = 80) -> str:
    """读取并去除首尾空白的字符串字段；缺省为空字符串，类型错误或超长抛 ValueError。"""
    value = data.get(key, "")
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"{key} 内容无效（最多 {limit} 字符）")
    return value.strip()


def reject_unknown(data: Dict[str, Any], allowed: Iterable[str], message: str) -> None:
    if set(data) - set(allowed):
        raise ValueError(message)


def require_empty(data: Dict[str, Any]) -> None:
    if data:
        raise ValueError("请求体必须为空对象")
