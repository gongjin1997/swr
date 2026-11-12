"""收集渲染输入：环境档案、负载均衡入口、四层配置集（平台 → 项目 → 环境 → 资源绑定 → 服务）的版本与值。

提交发布时记录"快照"（用到的每个配置集版本、环境档案与负载均衡入口的内容、资源版本），审批人批准的就是这份快照；
worker 下发、回滚、重新发布都按快照重新渲染，期间别人修改配置不会改变已提交的发布。
快照不含敏感值：敏感值在下发时才按快照中的版本解密。
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


def _pinned_resources(store: Store, box: Optional[SecretBox], items: List[Dict[str, Any]], reveal: bool
                     ) -> Tuple[List[Dict[str, Any]], Dict[str, List[Tuple[str, str]]]]:
    """从快照恢复资源及其导出。返回 (resources, exports_by_kind)"""
    from . import resources as resource_types

    resources_by_id: Dict[int, Dict[str, Any]] = {}
    exports: Dict[str, List[Tuple[str, str]]] = {"env": [], "secret_env": []}

    for item in items:
        resource_id = item["resource_id"]
        version = item["version"]

        # 恢复资源版本
        resource_version = store.get_resource_version(resource_id, version)
        if not resource_version:
            raise ValueError(f"发布快照引用的资源版本已被删除（资源 ID {resource_id} v{version}）")

        resource = store.get_resource(resource_id)
        if not resource:
            raise ValueError(f"发布快照引用的资源已被删除（资源 ID {resource_id}）")

        # 解密属性（如果 reveal）
        attributes = resource_version.get("attributes") or {}
        if reveal and box:
            decrypted = {}
            for key, value in attributes.items():
                if key in resource_types.secret_attributes(resource["type"]):
                    try:
                        decrypted[key] = box.decrypt(value)
                    except Exception:
                        decrypted[key] = ""
                else:
                    decrypted[key] = value
            attributes = decrypted

        resources_by_id[resource_id] = {
            "id": resource_id,
            "type": resource["type"],
            "attributes": attributes,
        }

    # 计算每个资源的导出（包括父链）
    for item in items:
        resource_id = item["resource_id"]
        alias = item["alias"]

        # 恢复父链
        parent_chain_data = item.get("parent_chain") or []
        chain = []

        for chain_item in parent_chain_data:
            chain_resource_id = chain_item["resource_id"]
            chain_version = chain_item["version"]

            if chain_resource_id not in resources_by_id:
                # 恢复父链资源
                chain_resource_version = store.get_resource_version(chain_resource_id, chain_version)
                if not chain_resource_version:
                    continue

                chain_resource = store.get_resource(chain_resource_id)
                if not chain_resource:
                    continue

                chain_attributes = chain_resource_version.get("attributes") or {}
                if reveal and box:
                    decrypted = {}
                    for key, value in chain_attributes.items():
                        if key in resource_types.secret_attributes(chain_resource["type"]):
                            try:
                                decrypted[key] = box.decrypt(value)
                            except Exception:
                                decrypted[key] = ""
                        else:
                            decrypted[key] = value
                    chain_attributes = decrypted

                resources_by_id[chain_resource_id] = {
                    "id": chain_resource_id,
                    "type": chain_resource["type"],
                    "attributes": chain_attributes,
                }

            chain.append(resources_by_id[chain_resource_id])

        # 计算导出
        try:
            resource_exports = resource_types.compute_exports(chain)
            for kind in ("env", "secret_env"):
                exports[kind].extend(resource_exports.get(kind, []))
        except ValueError:
            # 导出计算失败，跳过
            continue

    return list(resources_by_id.values()), exports


def _current_bindings(store: Store, box: Optional[SecretBox], service_id: int, reveal: bool
                     ) -> Tuple[List[Dict[str, Any]], Dict[str, List[Tuple[str, str]]], List[Dict[str, Any]]]:
    """查询服务当前的资源绑定。返回 (bindings, exports_by_kind, resource_pins)"""
    from . import resources as resource_types

    bindings = store.list_service_resource_bindings(service_id)
    resources_by_id: Dict[int, Dict[str, Any]] = {}
    exports: Dict[str, List[Tuple[str, str]]] = {"env": [], "secret_env": []}
    resource_pins: List[Dict[str, Any]] = []

    for binding in bindings:
        resource = binding["resource"]
        resource_id = resource["id"]

        # 收集资源及其父链
        chain: List[Dict[str, Any]] = []

        # 构建父链（需要查询父资源）
        # 简化实现：仅包含当前资源，父链展开由 resource_types.parent_chain 完成
        attributes = resource.get("attributes") or {}

        if reveal and box:
            decrypted = {}
            for key, value in attributes.items():
                if key in resource_types.secret_attributes(resource["type"]):
                    try:
                        decrypted[key] = box.decrypt(value)
                    except Exception:
                        decrypted[key] = ""
                else:
                    decrypted[key] = value
            attributes = decrypted

        resources_by_id[resource_id] = {
            "id": resource_id,
            "type": resource["type"],
            "attributes": attributes,
            "scope_type": resource["scope_type"],
            "scope_id": resource["scope_id"],
        }

        chain.append(resources_by_id[resource_id])

        # 展开父链（如果有父资源）
        parent_chain_data: List[Dict[str, Any]] = []
        if resource.get("parent_resource_id"):
            try:
                # 查询父资源并添加到链中
                parent_id = resource["parent_resource_id"]
                parent_resource = store.get_resource(parent_id)
                if parent_resource:
                    parent_attributes = parent_resource.get("attributes") or {}
                    if reveal and box:
                        parent_decrypted = {}
                        for key, value in parent_attributes.items():
                            if key in resource_types.secret_attributes(parent_resource["type"]):
                                try:
                                    parent_decrypted[key] = box.decrypt(value)
                                except Exception:
                                    parent_decrypted[key] = ""
                            else:
                                parent_decrypted[key] = value
                        parent_attributes = parent_decrypted

                    parent_data = {
                        "id": parent_id,
                        "type": parent_resource["type"],
                        "attributes": parent_attributes,
                        "scope_type": parent_resource["scope_type"],
                        "scope_id": parent_resource["scope_id"],
                    }
                    resources_by_id[parent_id] = parent_data
                    chain.append(parent_data)
                    parent_chain_data.append({
                        "resource_id": parent_id,
                        "version": parent_resource.get("version") or 0,
                    })

                    # 递归查询祖父资源（如果有）
                    if parent_resource.get("parent_resource_id"):
                        grandparent_id = parent_resource["parent_resource_id"]
                        grandparent_resource = store.get_resource(grandparent_id)
                        if grandparent_resource:
                            grandparent_attributes = grandparent_resource.get("attributes") or {}
                            if reveal and box:
                                grandparent_decrypted = {}
                                for key, value in grandparent_attributes.items():
                                    if key in resource_types.secret_attributes(grandparent_resource["type"]):
                                        try:
                                            grandparent_decrypted[key] = box.decrypt(value)
                                        except Exception:
                                            grandparent_decrypted[key] = ""
                                    else:
                                        grandparent_decrypted[key] = value
                                grandparent_attributes = grandparent_decrypted

                            grandparent_data = {
                                "id": grandparent_id,
                                "type": grandparent_resource["type"],
                                "attributes": grandparent_attributes,
                                "scope_type": grandparent_resource["scope_type"],
                                "scope_id": grandparent_resource["scope_id"],
                            }
                            resources_by_id[grandparent_id] = grandparent_data
                            chain.append(grandparent_data)
                            parent_chain_data.append({
                                "resource_id": grandparent_id,
                                "version": grandparent_resource.get("version") or 0,
                            })
            except Exception:
                # 父链展开失败，仅使用当前资源
                pass

        # 计算导出
        try:
            resource_exports = resource_types.compute_exports(chain)
            for kind in ("env", "secret_env"):
                exports[kind].extend(resource_exports.get(kind, []))
        except ValueError:
            continue

        # 记录资源 pin（包括父链）
        resource_pins.append({
            "resource_id": resource_id,
            "alias": binding["alias"],
            "type": resource["type"],
            "version": resource.get("version") or 0,
            "parent_chain": parent_chain_data,
        })

    return bindings, exports, resource_pins


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


def _merge(sets: List[Dict[str, Any]], kind: str) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    merged: Dict[str, Dict[str, Any]] = {}
    origin: Dict[str, str] = {}
    warnings: List[str] = []
    for item in sorted((s for s in sets if s["kind"] == kind), key=lambda s: SCOPE_ORDER[s["scope_type"]]):
        for entry in item["entries"]:
            current = merged.get(entry["key"])
            if current is not None and current.get("locked"):
                warnings.append(f"{entry['key']} 已被{origin[entry['key']]}层锁定，下层的值未生效")
                continue
            merged[entry["key"]] = entry
            origin[entry["key"]] = {"platform": "平台", "project": "项目", "environment": "环境",
                                    "binding": "资源绑定", "service": "服务"}[item["scope_type"]]
    return merged, warnings


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
            deployment_id: Optional[int] = None, extra_sets: Optional[List[Dict[str, Any]]] = None,
            extra_bindings: Optional[List[Dict[str, Any]]] = None
            ) -> Tuple[RenderInputs, Dict[str, Any]]:
    """返回 (渲染输入, 快照)。pinned 为已提交发布的快照；extra_sets 用于新建前的预览（尚未保存的服务层配置）；
    extra_bindings 用于新建前的预览（尚未保存的资源绑定）。"""
    namespace = store.get_namespace(service["namespace_id"])
    if not namespace:
        raise ValueError("服务所在的环境不存在")

    # 处理配置集
    if pinned:
        snapshot = {key: copy.deepcopy(pinned.get(key)) for key in
                    ("profile", "profile_version", "ingress_profile_id", "ingress", "ingress_version")}
        sets = _pinned_sets(store, pinned.get("config") or [])
    else:
        snapshot = current_snapshot(store, service)
        sets = [s for s in store.list_config_sets(layers(service)) if s.get("version")] if service.get("id") else \
            [s for s in store.list_config_sets(layers(service)[:3]) if s.get("version")]
        sets += extra_sets or []

    # 处理资源绑定
    resource_exports: Dict[str, List[Tuple[str, str]]] = {"env": [], "secret_env": []}
    resource_pins: List[Dict[str, Any]] = []

    if pinned and "resources" in pinned:
        # 从快照恢复资源
        _, resource_exports = _pinned_resources(store, box, pinned["resources"], reveal)
        resource_pins = pinned["resources"]
    elif service.get("id"):
        # 查询当前绑定
        _, resource_exports, resource_pins = _current_bindings(store, box, service["id"], reveal)
    elif extra_bindings:
        # 新建服务预览：使用传入的绑定
        # 简化实现：不计算导出，留给后续完善
        pass

    # 将资源导出转换为配置集格式参与合并
    if resource_exports["env"] or resource_exports["secret_env"]:
        if resource_exports["env"]:
            binding_env_set = {
                "id": -1,
                "scope_type": "binding",
                "scope_id": 0,
                "kind": "env",
                "entries": [{"key": k, "value": v} for k, v in resource_exports["env"]]
            }
            sets.append(binding_env_set)

        if resource_exports["secret_env"]:
            binding_secret_set = {
                "id": -2,
                "scope_type": "binding",
                "scope_id": 0,
                "kind": "secret_env",
                "entries": [{"key": k, "size": len(v.encode("utf-8")), "ciphertext": v if not reveal else ""}
                           for k, v in resource_exports["secret_env"]]
            }
            sets.append(binding_secret_set)

    profile = snapshot["profile"]
    names = object_names(spec, service["name"], namespace["name"], profile)
    env_entries, warnings = _merge(sets, "env")
    secret_entries, secret_warnings = _merge(sets, "secret_env")
    warnings += secret_warnings
    used = {s["id"]: s for s in sets if s["kind"] in ("env", "secret_env") and s["id"] > 0}

    mounts: Dict[str, ConfigObject] = {}
    for mount in spec.get("mounts") or []:
        config = mount["config"]
        candidates = [s for s in sets if s["name"] == config and s["kind"] in FILE_KINDS
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
        source = {"set_id": chosen["id"], "scope_type": chosen["scope_type"], "scope_id": chosen["scope_id"],
                  "name": chosen["name"], "kind": chosen["kind"], "version": chosen["version"]}
        mounts[config] = ConfigObject(name=chosen["name"] if shared else names["properties"], secret=secret, data=data,
                                      shared=shared, source=source if shared else {})
        used[chosen["id"]] = chosen

    pins = [{"set_id": s["id"], "scope_type": s["scope_type"], "scope_id": s["scope_id"], "name": s["name"],
             "kind": s["kind"], "version": s["version"]}
            for s in sorted(used.values(), key=lambda s: (SCOPE_ORDER[s["scope_type"]], s["kind"], s["name"]))]
    snapshot["config"] = pins
    snapshot["resources"] = resource_pins

    # config_hash 包含配置集和资源
    config_digest = json.dumps([(p["set_id"], p["version"]) for p in pins])
    resource_digest = json.dumps([(r["resource_id"], r["version"]) for r in resource_pins])
    digest = hashlib.sha256((config_digest + resource_digest).encode("ascii")).hexdigest()[:16]

    inputs = RenderInputs(
        service_id=service.get("id") or 0, service_name=service["name"], namespace=namespace["name"], spec=spec,
        profile=profile, ingress=snapshot.get("ingress"), image=image,
        env={key: entry["value"] for key, entry in env_entries.items()},
        secret_env={key: secret_bytes(store, box, entry, reveal) for key, entry in secret_entries.items()},
        mounts=mounts, config_hash=digest, revision_id=revision_id, deployment_id=deployment_id, warnings=warnings)
    return inputs, snapshot


def check_render(inputs: RenderInputs, namespace: Dict[str, Any]) -> List[str]:
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
    return errors
