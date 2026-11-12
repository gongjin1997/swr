"""镜像版本号（tag）的识别、排序与自动递增。"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Pattern, Tuple

TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_NUM_RE = re.compile(r"\d+")
_TEMPLATE_VAR = re.compile(r"(\{date\}|\{seq\})")
IGNORED_TAGS = {"latest"}


def is_valid_tag(tag: str) -> bool:
    return bool(tag) and bool(TAG_RE.match(tag))


def version_key(tag: str):
    """按标签里出现的全部数字排序：1.0.10 > 1.0.9，v12 > v9。"""
    return tuple(int(n) for n in _NUM_RE.findall(tag)), tag


def template_regex(template: str, date: Optional[str] = None) -> Pattern:
    """把版本号模板（如 1.0.{seq}、{date}.{seq}）转换成正则，{seq} 是第一个捕获组。"""
    parts = []
    for piece in _TEMPLATE_VAR.split(template):
        if piece == "{date}":
            parts.append(re.escape(date) if date else r"\d{8}")
        elif piece == "{seq}":
            parts.append(r"(\d+)")
        else:
            parts.append(re.escape(piece))
    return re.compile("^" + "".join(parts) + "$")


def render_tag_template(template: str, date: str, seq: int) -> str:
    return template.replace("{date}", date).replace("{seq}", str(seq))


def validate_tag_template(template: str) -> None:
    if template.count("{seq}") != 1:
        raise ValueError("版本号模板必须包含且只包含一个 {seq}")
    rest = _TEMPLATE_VAR.sub("", template)
    if "{" in rest or "}" in rest:
        raise ValueError("版本号模板只支持 {date}（当天日期）和 {seq}（自增序号）两个变量")
    if not is_valid_tag(render_tag_template(template, "20240101", 1)):
        raise ValueError("版本号模板生成的版本号不合法（只能包含字母、数字、. _ -）")


def candidate_tags(tags: Iterable[str], tag_regex: str = "", tag_template: str = "") -> List[str]:
    """可以当作“历史版本”的标签：排除 latest 和不含数字的标签，再按正则/模板过滤。"""
    pattern: Optional[Pattern] = None
    if tag_regex:
        pattern = re.compile(tag_regex)
    elif tag_template:
        pattern = template_regex(tag_template)
    result = []
    for tag in tags:
        if tag in IGNORED_TAGS or not _NUM_RE.search(tag):
            continue
        if pattern and not pattern.search(tag):
            continue
        result.append(tag)
    return result


def sort_tags_desc(tags: Iterable[str], tag_regex: str = "", tag_template: str = "") -> List[str]:
    return sorted(candidate_tags(tags, tag_regex, tag_template), key=version_key, reverse=True)


def latest_by_version(tags: Iterable[str], tag_regex: str = "", tag_template: str = "") -> Optional[str]:
    ordered = sort_tags_desc(tags, tag_regex, tag_template)
    return ordered[0] if ordered else None


def _increment_span(tag: str):
    """找出要递增的数字：优先取最后一个“独立”的数字（前后不是字母，开头的 v 除外），
    这样 1.0.9-arm64 会变成 1.0.10-arm64，而不是 1.0.9-arm65。"""
    spans = [m.span() for m in _NUM_RE.finditer(tag)]

    def standalone(start: int, end: int) -> bool:
        before = tag[start - 1] if start > 0 else ""
        after = tag[end] if end < len(tag) else ""
        if before.isalpha() and not (start == 1 and before in "vV"):
            return False
        return not after.isalpha()

    pure = [span for span in spans if standalone(*span)]
    chosen = pure or spans
    return chosen[-1] if chosen else None


def _dotted_spans(tag: str, start: int, end: int) -> List[Tuple[int, int]]:
    """包含末位数字 tag[start:end] 的点分数字的各段位置，例如 v1.0.9-arm64 中的 1、0、9。
    向左只合并用“.”连接、前面不是字母的数字（开头的 v 除外），build20.1 中的 20 不算版本号的一位。"""
    spans = [(start, end)]
    pos = start
    while pos >= 2 and tag[pos - 1] == "." and tag[pos - 2].isdigit():
        seg_end = pos - 1
        seg_start = seg_end
        while seg_start > 0 and tag[seg_start - 1].isdigit():
            seg_start -= 1
        before = tag[seg_start - 1] if seg_start > 0 else ""
        if before.isalpha() and not (seg_start == 1 and before in "vV"):
            break
        spans.insert(0, (seg_start, seg_end))
        pos = seg_start
    return spans


def _width(digits: str) -> int:
    """补零的位（如 07）保持原来的位数，其余按实际数字输出。"""
    return len(digits) if len(digits) > 1 and digits.startswith("0") else 1


def increment_tag(tag: str, carry: bool = True) -> str:
    """版本号加 1。

    carry=True（逢十进一，默认）：除第一位外，每一位只用 0–9，加 1 后满 10 向前一位进 1：
    1.0.8 → 1.0.9 → 1.1.0，1.9.9 → 2.0.0，9.9.9 → 10.0.0，1.0.9-arm64 → 1.1.0-arm64，v12 → v13。
    已经大于 9 的位（按旧规则产生的版本号）在下一次进位：1.0.15 → 1.1.0。补零的位保持位数：1.0.08 → 1.0.09 → 1.1.00。

    carry=False（末位递增）：只把最后一位加 1：1.0.9 → 1.0.10。需要两位数的小版本号时用这种方式。"""
    span = _increment_span(tag)
    if span is None:
        return f"{tag}.1"
    start, end = span
    if not carry:
        digits = tag[start:end]
        return tag[:start] + str(int(digits) + 1).zfill(len(digits)) + tag[end:]
    spans = _dotted_spans(tag, start, end)
    values = [tag[s:e] for s, e in spans]
    numbers = [int(v) for v in values]
    index = len(numbers) - 1
    numbers[index] += 1
    while index > 0 and numbers[index] > 9:
        numbers[index] = 0
        numbers[index - 1] += 1
        index -= 1
    parts = [tag[:spans[0][0]]]
    for i, (_, e) in enumerate(spans):
        parts.append(str(numbers[i]).zfill(_width(values[i])))
        parts.append(tag[e:spans[i + 1][0]] if i + 1 < len(spans) else tag[e:])
    return "".join(parts)


def next_tag(existing: Iterable[str], latest: Optional[str], initial_tag: str = "1.0.0",
             tag_template: str = "", now: Optional[datetime] = None, carry: bool = True) -> str:
    """计算新版本号，并保证不与仓库已有标签冲突。carry 见 increment_tag。"""
    existing_set = set(existing)
    if tag_template:
        today = (now or datetime.now()).strftime("%Y%m%d")
        pattern = template_regex(tag_template, today)
        seqs = []
        for tag in existing_set:
            match = pattern.match(tag)
            if match:
                seqs.append(int(match.group(1)))
        seq = max(seqs) + 1 if seqs else 1
        candidate = render_tag_template(tag_template, today, seq)
        while candidate in existing_set:
            seq += 1
            candidate = render_tag_template(tag_template, today, seq)
        return candidate
    candidate = increment_tag(latest, carry) if latest else (initial_tag or "1.0.0")
    for _ in range(10000):
        if candidate not in existing_set:
            break
        candidate = increment_tag(candidate, carry)
    return candidate


_CREATED_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")


def parse_created(value: Optional[str]) -> Optional[float]:
    """解析镜像 config 里的 created 时间（RFC3339，可能带纳秒），返回时间戳。"""
    if not value:
        return None
    match = _CREATED_RE.match(value.strip())
    if not match:
        return None
    year, month, day, hour, minute, second = (int(x) for x in match.groups()[:6])
    frac = float(match.group(7)) if match.group(7) else 0.0
    tz = match.group(8)
    offset = 0
    if tz and tz != "Z":
        sign = 1 if tz[0] == "+" else -1
        offset = sign * (int(tz[1:3]) * 3600 + int(tz[-2:]) * 60)
    stamp = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc).timestamp()
    return stamp - offset + frac
