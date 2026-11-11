"""请求体字段校验的小工具。校验失败抛 ValueError，由全局处理器转换为 400。"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable

DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?")
DNS_SUBDOMAIN_RE = re.compile(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?)*")
# ConfigMap / Secret 的键（也是 envFrom 注入的变量名、挂载的文件名）
CONFIG_KEY_RE = re.compile(r"[-._a-zA-Z0-9]{1,253}")
QUANTITY_RE = re.compile(r"(?:[0-9]+(?:\.[0-9]+)?|[0-9]+m)(?:Ki|Mi|Gi|Ti|Pi|Ei|k|M|G|T)?")
LABEL_KEY_RE = re.compile(r"(?:[a-z0-9](?:[-a-z0-9.]{0,251}[a-z0-9])?/)?[A-Za-z0-9](?:[-A-Za-z0-9_.]{0,61}[A-Za-z0-9])?")
LABEL_VALUE_RE = re.compile(r"(?:[A-Za-z0-9](?:[-A-Za-z0-9_.]{0,61}[A-Za-z0-9])?)?")


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
