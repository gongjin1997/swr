"""分层配置集：平台 → 项目 → 环境 →（阶段 3）服务。

- 环境变量（env）、敏感环境变量（secret_env）：每层各一个，按层合并，下层覆盖上层；上层可锁定键，下层不能覆盖。
  合并结果对应附件中每个服务自己的 `<svc>-configmap`、`<svc>-secrets`。
- 配置文件（files）、敏感文件（secret_files）：按名称区分，可以有多个；下层同名配置集整体替换上层。
  对应附件中命名空间共享的公共配置 ConfigMap、证书 Secret。
- 敏感值逐项用 SecretBox 加密；敏感文件也可以引用证书（证书库中的文件版本）。
- 每次保存生成不可变的新版本；读取时敏感值打码，有 secret.view 权限的人可以查看明文（调用方记审计）。
"""
from __future__ import annotations

import base64
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..access import Principal
from ..errors import Forbidden, NotFound
from ..security import SecretBox
from ..store import Store
from ..validation import CONFIG_KEY_RE, DNS_SUBDOMAIN_RE, reject_unknown, text_field

SCOPES = ("platform", "project", "environment", "service")
SCOPE_LABELS = {"platform": "平台", "project": "项目", "environment": "环境", "service": "服务"}
KINDS = ("env", "secret_env", "files", "secret_files")
KIND_LABELS = {"env": "环境变量", "secret_env": "敏感环境变量", "files": "配置文件", "secret_files": "敏感文件"}
SECRET_KINDS = frozenset({"secret_env", "secret_files"})
# 每层只有一个环境变量集和一个敏感环境变量集，名称固定
SINGLETON_NAMES = {"env": "env", "secret_env": "secret-env"}
FILE_FORMATS = ("properties", "yaml", "json", "xml", "text")
MAX_SET_BYTES = 1000 * 1024  # 留出对象元数据的空间，单个 ConfigMap / Secret 上限为 1 MiB
MAX_ENTRIES = 500
TEXT_CERTIFICATE_KINDS = ("x509", "key")


# ------------------------------------------------------------------ 权限
def _scope_key(scope_type: str, scope_id: int) -> Tuple[str, int]:
    if scope_type not in SCOPES:
        raise ValueError(f"scope_type 必须是 {', '.join(SCOPES)} 之一")
    if scope_type == "platform":
        return scope_type, 0
    if not isinstance(scope_id, int) or isinstance(scope_id, bool) or scope_id <= 0:
        raise ValueError("scope_id 必须是正整数")
    return scope_type, scope_id


def can_view(me: Principal, scope_type: str, scope_id: int) -> bool:
    if scope_type == "platform":
        return (me.can_anywhere("config.manage") or me.can_anywhere("environment.config")
                or me.can_anywhere("namespace.view") or me.can_anywhere("project.view"))
    if scope_type == "project":
        return me.can_project(scope_id, "project.view")
    if scope_type == "environment":
        return me.can_namespace(scope_id, "namespace.view")
    return False


def can_edit(me: Principal, scope_type: str, scope_id: int) -> bool:
    if scope_type == "platform":
        return me.can("config.manage")
    if scope_type == "project":
        return me.can_project(scope_id, "config.manage")
    if scope_type == "environment":
        return me.can_namespace(scope_id, "environment.config")
    return False


def can_reveal(me: Principal, scope_type: str, scope_id: int) -> bool:
    if scope_type == "platform":
        return me.can("secret.view")
    if scope_type == "project":
        return me.can_project(scope_id, "secret.view")
    if scope_type == "environment":
        return me.can_namespace(scope_id, "secret.view")
    return False


def _check_scope_exists(store: Store, scope_type: str, scope_id: int) -> None:
    if scope_type == "project" and not store.get_project(scope_id):
        raise NotFound("项目不存在")
    if scope_type == "environment" and not store.get_namespace(scope_id):
        raise NotFound("环境不存在")
    if scope_type == "service":
        raise ValueError("服务层配置随服务一起维护（阶段 3），暂不能单独创建")


def config_set_or_404(store: Store, me: Principal, set_id: int, action: str = "view") -> Dict[str, Any]:
    item = store.get_config_set(set_id)
    if not item or not can_view(me, item["scope_type"], item["scope_id"]):
        raise NotFound("配置不存在")
    if action == "edit" and not can_edit(me, item["scope_type"], item["scope_id"]):
        raise Forbidden("没有修改该层配置的权限")
    if action == "reveal" and not can_reveal(me, item["scope_type"], item["scope_id"]):
        raise Forbidden("没有查看敏感配置的权限")
    return item


# ------------------------------------------------------------------ 条目校验
def _guess_format(name: str) -> str:
    lowered = name.lower()
    for suffix, fmt in ((".properties", "properties"), (".yaml", "yaml"), (".yml", "yaml"), (".json", "json"),
                        (".xml", "xml")):
        if lowered.endswith(suffix):
            return fmt
    return "text"


def normalize_entries(store: Store, box: SecretBox, kind: str, raw: Any, scope_type: str,
                      previous: Sequence[Dict[str, Any]] = ()) -> Tuple[List[Dict[str, Any]], List[Tuple[str, int, int]]]:
    """校验并加密条目，返回 (entries, 证书引用)。敏感条目可写 keep: true 沿用上一版本的值（界面不回显明文时使用）。"""
    if not isinstance(raw, list) or len(raw) > MAX_ENTRIES:
        raise ValueError(f"entries 必须是数组，最多 {MAX_ENTRIES} 项")
    old = {e["key"]: e for e in previous}
    entries: List[Dict[str, Any]] = []
    refs: List[Tuple[str, int, int]] = []
    seen = set()
    total = 0
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("每个条目必须是对象")
        reject_unknown(item, ("key", "value", "description", "locked", "format", "keep", "certificate_id",
                              "certificate_version"), "条目包含不支持的字段")
        key = item.get("key")
        if not isinstance(key, str) or not CONFIG_KEY_RE.fullmatch(key):
            raise ValueError(f"键 {key!r} 无效：只能包含字母、数字、-、_、.，最长 253 字符")
        if key in seen:
            raise ValueError(f"键 {key} 重复")
        seen.add(key)
        locked = item.get("locked", False)
        if not isinstance(locked, bool) or (locked and scope_type == "service"):
            raise ValueError("locked 必须是布尔值；服务层配置不能锁定")
        entry: Dict[str, Any] = {"key": key, "description": text_field(item, "description", 200), "locked": locked}
        if kind in ("env", "files"):
            value = item.get("value")
            if not isinstance(value, str):
                raise ValueError(f"{key} 缺少字符串 value")
            entry["value"] = value
            total += len(key) + len(value.encode("utf-8"))
            if kind == "files":
                fmt = item.get("format") or _guess_format(key)
                if fmt not in FILE_FORMATS:
                    raise ValueError(f"文件格式必须是 {', '.join(FILE_FORMATS)} 之一")
                entry["format"] = fmt
        else:
            sources = [name for name in ("value", "keep", "certificate_id") if item.get(name) not in (None, False)]
            if len(sources) != 1:
                raise ValueError(f"{key} 需要且只能提供 value、keep 或 certificate_id 之一")
            if "value" in sources:
                value = item["value"]
                if not isinstance(value, str) or not value:
                    raise ValueError(f"{key} 的 value 必须是非空字符串")
                entry["ciphertext"] = box.encrypt(value)
                entry["size"] = len(value.encode("utf-8"))
                total += len(key) + entry["size"]
            elif "keep" in sources:
                previous_entry = old.get(key)
                if item["keep"] is not True or not previous_entry:
                    raise ValueError(f"{key} 在上一版本中不存在，不能沿用旧值")
                for name in ("ciphertext", "certificate_id", "certificate_version_id", "certificate_version", "size"):
                    if name in previous_entry:
                        entry[name] = previous_entry[name]
                if previous_entry.get("certificate_id"):
                    refs.append((key, previous_entry["certificate_id"], previous_entry["certificate_version_id"]))
                total += len(key) + int(entry.get("size") or 0)
            else:
                certificate_id = item["certificate_id"]
                if not isinstance(certificate_id, int) or isinstance(certificate_id, bool):
                    raise ValueError(f"{key} 的 certificate_id 必须是整数")
                certificate = store.get_certificate(certificate_id)
                if not certificate:
                    raise ValueError(f"{key} 引用的证书不存在")
                number = item.get("certificate_version") or certificate["version"]
                version = store.get_certificate_version(certificate_id, number=number)
                if not version:
                    raise ValueError(f"{key} 引用的证书版本不存在")
                if kind == "secret_env" and certificate["kind"] not in TEXT_CERTIFICATE_KINDS:
                    raise ValueError(f"{key}：环境变量只能引用 PEM 文本格式的证书或私钥")
                entry.update(certificate_id=certificate_id, certificate_version_id=version["id"],
                             certificate_version=version["number"], size=version["size"])
                refs.append((key, certificate_id, version["id"]))
                total += len(key) + version["size"] * 4 // 3
        entries.append(entry)
    if total > MAX_SET_BYTES:
        raise ValueError(f"配置总大小 {total // 1024} KiB 超过上限 {MAX_SET_BYTES // 1024} KiB（Kubernetes 单个对象上限 1 MiB）")
    return entries, refs


def public_entries(store: Store, kind: str, entries: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """接口返回的条目：敏感值只返回是否已设置与来源，不返回密文。"""
    result = []
    names: Dict[int, str] = {}
    for entry in entries:
        item = {"key": entry["key"], "description": entry.get("description", ""), "locked": entry.get("locked", False)}
        if kind in SECRET_KINDS:
            item["secret"] = True
            if "certificate_id" in entry:
                cid = entry["certificate_id"]
                if cid not in names:
                    cert = store.get_certificate(cid)
                    names[cid] = cert["name"] if cert else ""
                item.update(source="certificate", certificate_id=cid, certificate_name=names[cid],
                            certificate_version=entry.get("certificate_version"))
            else:
                item["source"] = "value"
            item["size"] = entry.get("size")
        else:
            item["value"] = entry["value"]
            if kind == "files":
                item["format"] = entry.get("format", "text")
        result.append(item)
    return result


def public_config_set(store: Store, me: Principal, item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": item["id"], "scope_type": item["scope_type"], "scope_id": item["scope_id"], "name": item["name"],
        "kind": item["kind"], "description": item["description"], "version": item.get("version") or 0,
        "updated_at": item.get("version_created_at") or item["updated_at"], "created_at": item["created_at"],
        "entries": public_entries(store, item["kind"], item.get("entries") or []),
        "can_edit": can_edit(me, item["scope_type"], item["scope_id"]),
        "can_reveal": item["kind"] in SECRET_KINDS and can_reveal(me, item["scope_type"], item["scope_id"]),
    }


def _plain(store: Store, box: SecretBox, entry: Dict[str, Any]) -> Dict[str, Any]:
    """敏感条目的明文。证书文件是文本时直接返回，二进制文件返回 base64。"""
    if "ciphertext" in entry:
        return {"value": box.decrypt(entry["ciphertext"]), "encoding": "text"}
    version = store.get_certificate_version(entry["certificate_id"], version_id=entry["certificate_version_id"])
    data = base64.b64decode(box.decrypt(version["ciphertext"])) if version else b""
    try:
        return {"value": data.decode("utf-8"), "encoding": "text"}
    except UnicodeDecodeError:
        return {"value": base64.b64encode(data).decode("ascii"), "encoding": "base64"}


# ------------------------------------------------------------------ 读写
def list_config_sets(store: Store, me: Principal, scope_type: str, scope_id: int) -> List[Dict[str, Any]]:
    scope_type, scope_id = _scope_key(scope_type, scope_id)
    if not can_view(me, scope_type, scope_id):
        raise NotFound("没有可见的配置")
    return [public_config_set(store, me, item) for item in store.list_config_sets([(scope_type, scope_id)])]


def create_config_set(store: Store, box: SecretBox, me: Principal, data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("请求体必须是对象")
    reject_unknown(data, ("scope_type", "scope_id", "name", "kind", "description", "entries", "note"),
                   "包含不支持的字段")
    scope_type, scope_id = _scope_key(data.get("scope_type"), data.get("scope_id", 0))
    kind = data.get("kind")
    if kind not in KINDS:
        raise ValueError(f"kind 必须是 {', '.join(KINDS)} 之一")
    if not can_view(me, scope_type, scope_id):
        raise NotFound("没有可见的配置")
    if not can_edit(me, scope_type, scope_id):
        raise Forbidden("没有修改该层配置的权限")
    _check_scope_exists(store, scope_type, scope_id)
    name = SINGLETON_NAMES.get(kind) or data.get("name")
    if not isinstance(name, str) or len(name) > 253 or not DNS_SUBDOMAIN_RE.fullmatch(name):
        raise ValueError("配置名称会作为 ConfigMap / Secret 名称，必须是小写字母、数字、-、. 组成的 DNS 名称")
    if kind not in SINGLETON_NAMES and name in SINGLETON_NAMES.values():
        raise ValueError(f"名称 {name} 保留给每层的环境变量配置")
    entries, refs = normalize_entries(store, box, kind, data.get("entries", []), scope_type)
    _check_locked(store, scope_type, scope_id, kind, entries)
    set_id = store.create_config_set(scope_type, scope_id, name, kind, text_field(data, "description", 300), entries,
                                     refs, text_field(data, "note", 300), me.id)
    return public_config_set(store, me, store.get_config_set(set_id))


def update_config_set(store: Store, box: SecretBox, me: Principal, set_id: int, data: Any) -> Dict[str, Any]:
    item = config_set_or_404(store, me, set_id, "edit")
    if not isinstance(data, dict):
        raise ValueError("请求体必须是对象")
    reject_unknown(data, ("base_version", "entries", "note", "description"), "包含不支持的字段")
    base_version = data.get("base_version")
    if not isinstance(base_version, int) or isinstance(base_version, bool):
        raise ValueError("base_version 必须是整数（当前编辑基于的版本号）")
    entries, refs = normalize_entries(store, box, item["kind"], data.get("entries", []), item["scope_type"],
                                      item.get("entries") or [])
    _check_locked(store, item["scope_type"], item["scope_id"], item["kind"], entries)
    description = text_field(data, "description", 300) if "description" in data else None
    store.add_config_version(set_id, base_version, entries, refs, text_field(data, "note", 300), me.id, description)
    return public_config_set(store, me, store.get_config_set(set_id))


def delete_config_set(store: Store, me: Principal, set_id: int) -> Dict[str, Any]:
    item = config_set_or_404(store, me, set_id, "edit")
    store.delete_config_set(set_id)
    return item


def versions(store: Store, me: Principal, set_id: int) -> List[Dict[str, Any]]:
    config_set_or_404(store, me, set_id)
    return store.list_config_versions(set_id)


def version_detail(store: Store, me: Principal, set_id: int, number: int) -> Dict[str, Any]:
    item = config_set_or_404(store, me, set_id)
    version = store.get_config_version(set_id, number)
    if not version:
        raise NotFound("版本不存在")
    return {"number": version["number"], "note": version["note"], "created_at": version["created_at"],
            "created_by": version["created_by"], "entries": public_entries(store, item["kind"], version["entries"])}


def diff(store: Store, box: SecretBox, me: Principal, set_id: int, old: int, new: int) -> List[Dict[str, Any]]:
    """两个版本的差异。敏感值只给出是否变化，不返回值。"""
    item = config_set_or_404(store, me, set_id)
    left, right = store.get_config_version(set_id, old), store.get_config_version(set_id, new)
    if not left or not right:
        raise NotFound("版本不存在")
    secret = item["kind"] in SECRET_KINDS
    before = {e["key"]: e for e in left["entries"]}
    after = {e["key"]: e for e in right["entries"]}
    changes = []
    for key in list(before) + [k for k in after if k not in before]:
        a, b = before.get(key), after.get(key)
        if a is None or b is None:
            change = "added" if a is None else "removed"
        elif secret:
            change = "changed" if _plain(store, box, a) != _plain(store, box, b) else ""
        else:
            change = "changed" if (a["value"], a.get("format")) != (b["value"], b.get("format")) else ""
        if not change and a and b and (a.get("locked"), a.get("description")) != (b.get("locked"), b.get("description")):
            change = "changed"
        if change:
            row: Dict[str, Any] = {"key": key, "change": change}
            if not secret:
                row.update(old=a["value"] if a else None, new=b["value"] if b else None)
            changes.append(row)
    return changes


def reveal(store: Store, box: SecretBox, me: Principal, set_id: int, keys: Optional[List[str]] = None,
           number: Optional[int] = None) -> Dict[str, Dict[str, Any]]:
    item = config_set_or_404(store, me, set_id, "reveal")
    if item["kind"] not in SECRET_KINDS:
        raise ValueError("只有敏感配置需要单独查看")
    entries = item["entries"]
    if number is not None:
        version = store.get_config_version(set_id, number)
        if not version:
            raise NotFound("版本不存在")
        entries = version["entries"]
    wanted = set(keys) if keys else None
    return {e["key"]: _plain(store, box, e) for e in entries if wanted is None or e["key"] in wanted}


# ------------------------------------------------------------------ 分层合并
def _layers(project_id: Optional[int], namespace_id: Optional[int]) -> List[Tuple[str, int]]:
    layers = [("platform", 0)]
    if project_id:
        layers.append(("project", project_id))
    if namespace_id:
        layers.append(("environment", namespace_id))
    return layers


def _check_locked(store: Store, scope_type: str, scope_id: int, kind: str, entries: List[Dict[str, Any]]) -> None:
    """下层不能覆盖上层锁定的键。项目层与环境层的上层分别是平台层、平台层加该环境所属的项目层。"""
    if kind not in SINGLETON_NAMES or scope_type == "platform":
        return
    uppers: List[Tuple[str, int]] = [("platform", 0)]
    if scope_type == "environment":
        namespace = store.get_namespace(scope_id) or {}
        uppers += [("project", pid) for pid in namespace.get("project_ids", [])]
    locked = {}
    for upper in store.list_config_sets(uppers, kind=kind):
        for entry in upper["entries"]:
            if entry.get("locked"):
                locked[entry["key"]] = SCOPE_LABELS[upper["scope_type"]]
    clashes = sorted(e["key"] for e in entries if e["key"] in locked)
    if clashes:
        raise ValueError("以下键已被上层锁定，不能覆盖：" + "、".join(f"{k}（{locked[k]}）" for k in clashes))


def effective_config(store: Store, me: Principal, namespace_id: int, project_id: Optional[int] = None) -> Dict[str, Any]:
    """环境（可选再叠加一个项目）生效的配置：每个键的 origin 注明来自哪一层，overrides 是被它覆盖的上层。"""
    sets = store.list_config_sets(_layers(project_id, namespace_id))
    order = {scope: index for index, scope in enumerate(SCOPES)}
    sets.sort(key=lambda s: order[s["scope_type"]])
    merged: Dict[str, Dict[str, Dict[str, Any]]] = {"env": {}, "secret_env": {}}
    files: Dict[str, Dict[str, Any]] = {}
    warnings: List[str] = []
    for item in sets:
        origin = {"scope_type": item["scope_type"], "scope_id": item["scope_id"], "config_set_id": item["id"],
                  "version": item.get("version") or 0}
        if item["kind"] in merged:
            target = merged[item["kind"]]
            for entry in public_entries(store, item["kind"], item["entries"]):
                current = target.get(entry["key"])
                if current and current["locked"]:
                    warnings.append(f"{entry['key']} 被{SCOPE_LABELS[current['origin']['scope_type']]}层锁定，"
                                    f"{SCOPE_LABELS[item['scope_type']]}层的值未生效")
                    continue
                overridden = ([current["origin"]] + current.get("overrides", [])) if current else []
                target[entry["key"]] = dict(entry, origin=origin, overrides=overridden)
        else:
            replaced = files.get(item["name"])
            files[item["name"]] = {"name": item["name"], "kind": item["kind"], "origin": origin,
                                   "keys": [e["key"] for e in item["entries"]],
                                   "overrides": [replaced["origin"]] if replaced else []}
    return {"env": list(merged["env"].values()), "secret_env": list(merged["secret_env"].values()),
            "files": list(files.values()), "warnings": warnings}


# ------------------------------------------------------------------ properties 文件
_PROPERTY_SPLIT = re.compile(r"(?<!\\)[=:]|(?<!\\)\s")


def parse_properties(text: str) -> List[Tuple[str, str]]:
    """解析 Java properties：支持 # 与 ! 注释、= / : / 空白分隔、行尾 \\ 续行。值不做转义还原，便于原样比较。"""
    result: List[Tuple[str, str]] = []
    logical = ""
    for raw in text.splitlines():
        line = raw.strip() if not logical else raw.lstrip()
        if not logical and (not line or line[0] in "#!"):
            continue
        if line.endswith("\\") and not line.endswith("\\\\"):
            logical += line[:-1]
            continue
        logical += line
        match = _PROPERTY_SPLIT.search(logical)
        if match:
            key = logical[:match.start()].strip()
            value = logical[match.end():].lstrip()
            if match.group().isspace() and value[:1] in ("=", ":"):
                value = value[1:].lstrip()
        else:
            key, value = logical.strip(), ""
        if key:
            result.append((key, value))
        logical = ""
    if logical.strip():
        result.append((logical.strip(), ""))
    return result


def _compare_maps(left: Dict[str, Any], right: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for key in list(left) + [k for k in right if k not in left]:
        a, b = left.get(key), right.get(key)
        status = "left_only" if b is None else "right_only" if a is None else ("same" if a == b else "different")
        rows.append({"key": key, "left": a, "right": b, "status": status})
    return rows


def compare_environments(store: Store, box: SecretBox, me: Principal, left_id: int, right_id: int,
                         project_id: Optional[int] = None) -> Dict[str, Any]:
    """比较两个环境的生效配置。敏感值只在对两边都有查看权限时比较（不返回明文）。"""
    sides = []
    for namespace_id in (left_id, right_id):
        if not store.get_namespace(namespace_id) or not me.can_namespace(namespace_id, "namespace.view"):
            raise NotFound("环境不存在")
        sides.append(store.list_config_sets(_layers(project_id, namespace_id)))
    order = {scope: index for index, scope in enumerate(SCOPES)}

    def flatten(sets: List[Dict[str, Any]], kind: str) -> Dict[str, Any]:
        values: Dict[str, Any] = {}
        for item in sorted((s for s in sets if s["kind"] == kind), key=lambda s: order[s["scope_type"]]):
            for entry in item["entries"]:
                values[entry["key"]] = entry
        return values

    result: Dict[str, Any] = {"env": [], "secret_env": [], "files": []}
    left_env, right_env = flatten(sides[0], "env"), flatten(sides[1], "env")
    result["env"] = _compare_maps({k: v["value"] for k, v in left_env.items()},
                                  {k: v["value"] for k, v in right_env.items()})
    secrets_visible = can_reveal(me, "environment", left_id) and can_reveal(me, "environment", right_id)
    left_secret, right_secret = flatten(sides[0], "secret_env"), flatten(sides[1], "secret_env")
    rows = _compare_maps({k: True for k in left_secret}, {k: True for k in right_secret})
    for row in rows:
        if row["status"] == "same":
            row["status"] = ("same" if _plain(store, box, left_secret[row["key"]]) == _plain(store, box, right_secret[row["key"]])
                             else "different") if secrets_visible else "hidden"
        row["left"] = row["right"] = None
    result["secret_env"] = rows

    def file_sets(sets: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        named: Dict[str, Dict[str, Any]] = {}
        for item in sorted((s for s in sets if s["kind"] in ("files", "secret_files")), key=lambda s: order[s["scope_type"]]):
            named[item["name"]] = item
        return named

    left_files, right_files = file_sets(sides[0]), file_sets(sides[1])
    for name in list(left_files) + [n for n in right_files if n not in left_files]:
        a, b = left_files.get(name), right_files.get(name)
        entry: Dict[str, Any] = {"name": name, "status": "left_only" if b is None else "right_only" if a is None else "",
                                 "files": []}
        if a and b:
            kind = a["kind"]
            a_entries, b_entries = {e["key"]: e for e in a["entries"]}, {e["key"]: e for e in b["entries"]}
            for key in list(a_entries) + [k for k in b_entries if k not in a_entries]:
                ea, eb = a_entries.get(key), b_entries.get(key)
                file_row: Dict[str, Any] = {"key": key}
                if ea is None or eb is None:
                    file_row["status"] = "left_only" if eb is None else "right_only"
                elif kind == "files":
                    if ea.get("format") == "properties" or eb.get("format") == "properties":
                        props = _compare_maps(dict(parse_properties(ea["value"])), dict(parse_properties(eb["value"])))
                        file_row["properties"] = [p for p in props if p["status"] != "same"]
                        file_row["status"] = "different" if file_row["properties"] else "same"
                    else:
                        file_row["status"] = "same" if ea["value"] == eb["value"] else "different"
                else:
                    file_row["status"] = ("same" if _plain(store, box, ea) == _plain(store, box, eb) else "different") \
                        if secrets_visible else "hidden"
                entry["files"].append(file_row)
            entry["status"] = "same" if all(f["status"] == "same" for f in entry["files"]) else "different"
        result["files"].append(entry)
    return result
