"""收集渲染输入：环境档案、负载均衡入口、四层配置集（平台 → 项目 → 环境 → 资源绑定 → 服务）的版本与值。

提交发布时记录"快照"（用到的每个配置集版本、资源版本、环境档案与负载均衡入口的内容），审批人批准的就是这份快照；
worker 下发、回滚、重新发布都按快照重新渲染，期间别人修改配置不会改变已提交的发布。
快照不含敏感值：敏感值在下发时才按快照中的版本解密。

来源追踪：每个配置 key 记录来自哪一层、哪个配置集或资源、版本号、是否被锁定、是否被覆盖。
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from ..security import SecretBox
from ..store import Store
from .render import ConfigObject, RenderInputs
from .resources import compute_exports
from .spec import SERVICE_FILES, check_resources, effective_resources, ingress_host, object_names

SCOPE_ORDER = {"platform": 0, "project": 1, "environment": 2, "binding": 2.5, "service": 3}
MAX_OBJECT_BYTES = 1000 * 1024
FILE_KINDS = ("files", "secret_files")


def layers(service: Dict[str, Any]) -> List[Tuple[str, int]]:
    return [("platform", 0), ("project", service["project_id"]), ("environment", service["namespace_id"]),
            ("service", service["id"])]


def _pinned_sets(store: Store, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    sets = []
    for item in items:
        version = store.get_config_version(item["set_id"], item["version"])
        if not version:
            raise ValueError(f"发布快照引用的配置 {item['name']} v{item['version']} 已被删除，不能按快照重新下发")
        sets.append({"id": item["set_id"], "scope_type": item["scope_type"], "scope_id": item["scope_id"],
                     "name": item["name"], "kind": item["kind"], "version": item["version"],
                     "entries": version["entries"]})
    return sets


def secret_bytes(store: Store, box: Optional[SecretBox], entry: Dict[str, Any], reveal: bool) -> bytes:
    """敏感条目的值。reveal=False 时返回同样长度的占位内容（预览与服务端校验用），不解密。"""
    if not reveal or box is None:
        return b"*" * max(int(entry.get("size") or 0), 1)
    if "ciphertext" in entry:
        text = box.decrypt(entry["ciphertext"])
        return base64.b64decode(text) if entry.get("encoding") == "base64" else text.encode("utf-8")
    version = store.get_certificate_version(entry["certificate_id"], version_id=entry["certificate_version_id"])
    if not version:
        raise ValueError(f"{entry['key']} 引用的证书版本已不存在")
    return base64.b64decode(box.decrypt(version["ciphertext"]))


def _merge(sets: List[Dict[str, Any]], kind: str) -> Tuple[Dict[str, Dict[str, Any]], List[str], Dict[str, Dict[str, Any]]]:
    """
    合并配置集，返回 (合并后的条目, 警告列表, 来源追踪)。

    来源追踪格式: {key: {source: "平台|项目|环境|资源绑定|服务",
                        source_type: "config_set|resource",
                        source_id: int, source_name: str, version: int,
                        locked: bool, overridden: bool}}
    """
    merged: Dict[str, Dict[str, Any]] = {}
    origin: Dict[str, Dict[str, Any]] = {}
    warnings: List[str] = []

    scope_labels = {"platform": "平台", "project": "项目", "environment": "环境",
                    "binding": "资源绑定", "service": "服务"}

    for item in sorted((s for s in sets if s["kind"] == kind), key=lambda s: SCOPE_ORDER[s["scope_type"]]):
        for entry in item["entries"]:
            key = entry["key"]
            current = merged.get(key)

            # 记录来源
            source_info = {
                "source": scope_labels[item["scope_type"]],
                "source_type": item.get("source_type", "config_set"),
                "source_id": item.get("source_id", item.get("id")),
                "source_name": item["name"],
                "version": item.get("version", 1),
                "locked": entry.get("locked", False),
                "overridden": False,
            }

            if current is not None and current.get("locked"):
                # 已被锁定，标记为被覆盖
                source_info["overridden"] = True
                warnings.append(f"{key} 已被{origin[key]['source']}层锁定，下层的值未生效")
                # 不更新 merged，但记录来源
                if key not in origin:
                    origin[key] = source_info
                continue

            # 如果之前有值，标记为被覆盖
            if key in origin:
                origin[key]["overridden"] = True

            merged[key] = entry
            origin[key] = source_info

    return merged, warnings, origin


def current_snapshot(store: Store, service: Dict[str, Any]) -> Dict[str, Any]:
    """当前的环境档案与负载均衡入口（不含配置集）。"""
    from ..services.environments import merged_profile

    row = store.get_environment_profile(service["namespace_id"])
    ingress = store.get_ingress_profile(row["ingress_profile_id"]) if row and row.get("ingress_profile_id") else None
    return {"profile": merged_profile(row["profile"] if row else None), "profile_version": row["version"] if row else 0,
            "ingress_profile_id": ingress["id"] if ingress else None, "ingress": ingress["spec"] if ingress else None,
            "ingress_version": ingress["version"] if ingress else 0}


def resolve(store: Store, box: Optional[SecretBox], service: Dict[str, Any], spec: Dict[str, Any], image: str, *,
            pinned: Optional[Dict[str, Any]] = None, reveal: bool = False, revision_id: Optional[int] = None,
            deployment_id: Optional[int] = None, extra_sets: Optional[List[Dict[str, Any]]] = None
            ) -> Tuple[RenderInputs, Dict[str, Any]]:
    """返回 (渲染输入, 快照)。pinned 为已提交发布的快照；extra_sets 用于新建前的预览（尚未保存的服务层配置）。"""
    from ..services import resources as resource_service

    namespace = store.get_namespace(service["namespace_id"])
    if not namespace:
        raise ValueError("服务所在的环境不存在")

    if pinned:
        snapshot = {key: copy.deepcopy(pinned.get(key)) for key in
                    ("profile", "profile_version", "ingress_profile_id", "ingress", "ingress_version")}
        sets = _pinned_sets(store, pinned.get("config") or [])
        # 从快照恢复资源绑定
        resource_sets = []
        if pinned.get("resources"):
            resources_by_id = {}
            for res_pin in pinned["resources"]:
                version = store.get_resource_version(res_pin["resource_id"], res_pin["version"])
                if version:
                    resources_by_id[res_pin["resource_id"]] = {
                        "id": version["resource_id"],
                        "type": version["type"],
                        "name": version["name"],
                        "parent_id": version["parent_id"],
                        "attributes": resource_service._decrypt_attributes(box, version["type"], version["attributes"]) if box else version["attributes"],
                        "current_version_id": version["id"],
                    }
            # 构建资源链并导出
            for res_pin in pinned["resources"]:
                try:
                    from .resources import parent_chain
                    chain = parent_chain(resources_by_id, res_pin["resource_id"])
                    exports = compute_exports(chain)
                    for kind, entries in exports.items():
                        if entries:
                            resource_sets.append({
                                "id": res_pin["resource_id"],
                                "source_id": res_pin["resource_id"],
                                "source_type": "resource",
                                "scope_type": "binding",
                                "scope_id": 0,
                                "name": chain[0]["name"],
                                "kind": kind,
                                "version": res_pin["version"],
                                "entries": [{"key": k, "value": v, "locked": False} for k, v, subpath in entries],
                            })
                except (ValueError, KeyError):
                    continue
    else:
        snapshot = current_snapshot(store, service)
        sets = [s for s in store.list_config_sets(layers(service)) if s.get("version")] if service.get("id") else \
            [s for s in store.list_config_sets(layers(service)[:3]) if s.get("version")]

        # 添加资源绑定导出
        resource_sets = []
        if service.get("id") and box:
            chains, version_pins = resource_service.resolve_bindings(store, box, service["id"])
            resource_pins = []
            for chain in chains:
                resource_id = chain[0]["id"]
                exports = compute_exports(chain)
                for kind, entries in exports.items():
                    if entries:
                        # env 和 files 类导出
                        if kind in ("env", "files"):
                            resource_sets.append({
                                "id": resource_id,
                                "source_id": resource_id,
                                "source_type": "resource",
                                "scope_type": "binding",
                                "scope_id": 0,
                                "name": chain[0]["name"],
                                "kind": kind,
                                "version": version_pins.get(resource_id, 1),
                                "entries": [{"key": k, "value": v, "locked": False} for k, v, subpath in entries if subpath is None],
                            })
                        # secret_env 导出
                        elif kind == "secret_env":
                            resource_sets.append({
                                "id": resource_id,
                                "source_id": resource_id,
                                "source_type": "resource",
                                "scope_type": "binding",
                                "scope_id": 0,
                                "name": chain[0]["name"],
                                "kind": kind,
                                "version": version_pins.get(resource_id, 1),
                                "entries": [{"key": k, "value": v.encode("utf-8") if isinstance(v, str) else v, "size": len(v)} for k, v, subpath in entries if subpath is None],
                            })
                        # files 带 subpath 的导出（properties 等）
                        if kind == "files":
                            properties_entries = [(k, v) for k, v, subpath in entries if subpath == "properties"]
                            if properties_entries:
                                resource_sets.append({
                                    "id": resource_id,
                                    "source_id": resource_id,
                                    "source_type": "resource",
                                    "scope_type": "binding",
                                    "scope_id": 0,
                                    "name": chain[0]["name"],
                                    "kind": "files",
                                    "subpath": "properties",
                                    "version": version_pins.get(resource_id, 1),
                                    "entries": [{"key": k, "value": v, "locked": False} for k, v in properties_entries],
                                })
                resource_pins.append({"resource_id": resource_id, "version": version_pins.get(resource_id, 1)})
            snapshot["resources"] = resource_pins

        sets += extra_sets or []

    # 合并所有配置集（包括资源导出）
    all_sets = sets + resource_sets

    profile = snapshot["profile"]
    names = object_names(spec, service["name"], namespace["name"], profile)
    env_entries, warnings, env_sources = _merge(all_sets, "env")
    secret_entries, secret_warnings, secret_sources = _merge(all_sets, "secret_env")
    warnings += secret_warnings
    used = {s["id"]: s for s in all_sets if s["kind"] in ("env", "secret_env")}

    mounts: Dict[str, ConfigObject] = {}
    for mount in spec.get("mounts") or []:
        config = mount["config"]
        candidates = [s for s in all_sets if s["name"] == config and s["kind"] in FILE_KINDS
                      and (s["scope_type"] != "service" or config == SERVICE_FILES)]
        if not candidates:
            raise ValueError(f"挂载引用的配置 {config} 在平台、项目、环境层都不存在")
        chosen = max(candidates, key=lambda s: SCOPE_ORDER[s["scope_type"]])
        keys = {e["key"] for e in chosen["entries"]}
        missing = [item for item in mount.get("items") or [] if item not in keys]
        if missing:
            raise ValueError(f"配置 {config} 中没有文件 {'、'.join(missing)}")
        if config in mounts:
            continue
        secret = chosen["kind"] == "secret_files"
        data = {e["key"]: (secret_bytes(store, box, e, reveal) if secret else e["value"].encode("utf-8"))
                for e in chosen["entries"]}
        shared = chosen["scope_type"] != "service"
        source = {"set_id": chosen.get("source_id", chosen["id"]), "scope_type": chosen["scope_type"],
                  "scope_id": chosen["scope_id"], "name": chosen["name"], "kind": chosen["kind"],
                  "version": chosen["version"], "source_type": chosen.get("source_type", "config_set")}
        mounts[config] = ConfigObject(name=chosen["name"] if shared else names["properties"], secret=secret, data=data,
                                      shared=shared, source=source if shared else {})
        used[chosen["id"]] = chosen

    # 快照中记录所有使用的配置集和资源版本
    config_pins = [{"set_id": s["id"], "scope_type": s["scope_type"], "scope_id": s["scope_id"], "name": s["name"],
                    "kind": s["kind"], "version": s["version"]}
                   for s in sorted([s for s in used.values() if s.get("source_type", "config_set") == "config_set"],
                                   key=lambda s: (SCOPE_ORDER[s["scope_type"]], s["kind"], s["name"]))]
    snapshot["config"] = config_pins

    # 计算 config_hash：包括配置集和资源版本
    all_pins = [(p["set_id"], p["version"]) for p in config_pins]
    if snapshot.get("resources"):
        all_pins += [(r["resource_id"], r["version"]) for r in snapshot["resources"]]
    digest = hashlib.sha256(json.dumps(all_pins).encode("ascii")).hexdigest()[:16]

    # 将来源追踪附加到 snapshot
    snapshot["sources"] = {"env": env_sources, "secret_env": secret_sources}

    inputs = RenderInputs(
        service_id=service.get("id") or 0, service_name=service["name"], namespace=namespace["name"], spec=spec,
        profile=profile, ingress=snapshot.get("ingress"), image=image,
        env={key: entry["value"] for key, entry in env_entries.items()},
        secret_env={key: secret_bytes(store, box, entry, reveal) for key, entry in secret_entries.items()},
        mounts=mounts, config_hash=digest, revision_id=revision_id, deployment_id=deployment_id, warnings=warnings)
    return inputs, snapshot


def check_render(inputs: RenderInputs, namespace: Dict[str, Any], snapshot: Optional[Dict[str, Any]] = None) -> List[str]:
    """依赖环境档案、负载均衡入口与命名空间策略的检查，返回错误列表（空列表表示可以下发）。"""
    from ..services.namespaces import effective_policy

    policy = effective_policy(namespace["policy"])
    spec, profile = inputs.spec, inputs.profile
    errors: List[str] = []
    try:
        object_names(spec, inputs.service_name, inputs.namespace, profile)
    except ValueError as exc:
        errors.append(str(exc))
    errors += check_resources(effective_resources(spec, profile), policy)
    allowed_secrets = policy.get("image_pull_secrets") or []
    if profile.get("image_pull_secret") and allowed_secrets and profile["image_pull_secret"] not in allowed_secrets:
        errors.append(f"镜像拉取密钥 {profile['image_pull_secret']} 不在命名空间策略允许的范围内")
    if spec["ingress"]["enabled"]:
        if not inputs.ingress:
            errors.append("环境没有配置负载均衡入口，不能启用 Ingress")
        else:
            host = ingress_host(spec, inputs.service_name, inputs.namespace, profile)
            suffixes = policy["ingress"].get("host_suffixes") or []
            if suffixes and not any(host.endswith(s) for s in suffixes):
                errors.append(f"Ingress 主机名 {host} 不符合命名空间策略允许的域名后缀")
            required_class = policy["ingress"].get("ingress_class")
            if required_class and inputs.ingress.get("ingress_class") != required_class:
                errors.append(f"负载均衡入口的 IngressClass 必须是 {required_class}")
    replicas = spec["replicas"] if spec["replicas"] is not None else (profile.get("defaults") or {}).get("replicas", 1)
    if not spec["hpa"]["enabled"] and replicas > policy["max_replicas"]:
        errors.append(f"副本数 {replicas} 超过命名空间策略上限 {policy['max_replicas']}")
    for obj in list(inputs.mounts.values()):
        size = sum(len(k) + len(v) for k, v in obj.data.items())
        if size > MAX_OBJECT_BYTES:
            errors.append(f"配置 {obj.name} 共 {size // 1024} KiB，超过 Kubernetes 单个对象 1 MiB 的上限")
    for label, data in (("环境变量", {k: v.encode("utf-8") for k, v in inputs.env.items()}), ("敏感变量", inputs.secret_env)):
        if sum(len(k) + len(v) for k, v in data.items()) > MAX_OBJECT_BYTES:
            errors.append(f"合并后的{label}超过 Kubernetes 单个对象 1 MiB 的上限")

    # 检查必填参数（从快照的来源追踪中提取）
    if snapshot and snapshot.get("required_params"):
        all_keys = set(inputs.env.keys()) | set(inputs.secret_env.keys())
        for mount_obj in inputs.mounts.values():
            all_keys.update(mount_obj.data.keys())
        missing = [key for key in snapshot["required_params"] if key not in all_keys]
        if missing:
            errors.append(f"缺少必填参数: {', '.join(missing[:5])}" + ("..." if len(missing) > 5 else ""))

    return errors
