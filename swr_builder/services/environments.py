"""环境：已纳管的命名空间 + 环境档案 + 环境层配置。

环境档案保存与环境强相关的结构参数（附件中每个服务都相同、只随环境变化的部分）：
对象命名规则、主机名规则、负载均衡入口、节点池调度、镜像拉取密钥、日志目录、资源与副本默认值。
应用参数（环境变量、敏感变量、公共配置文件）在环境层配置集里维护，见 services/configsets.py。
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional

from ..access import Principal
from ..errors import Forbidden, NotFound
from ..store import Store
from ..validation import (DNS_LABEL_RE, DNS_SUBDOMAIN_RE, LABEL_KEY_RE, LABEL_VALUE_RE, QUANTITY_RE, reject_unknown,
                          text_field)
from . import configsets
from .access import namespace_or_404

NAMING_KEYS = ("deployment", "service", "ingress", "configmap", "secret", "properties")
DEFAULT_PROFILE: Dict[str, Any] = {
    # {base} 默认等于服务名，{namespace} 为命名空间名；与附件的命名一致
    "naming": {"deployment": "{base}-app", "service": "{base}-service", "ingress": "{base}-ingress",
               "configmap": "{base}-configmap", "secret": "{base}-secrets", "properties": "{base}-properties"},
    "host": "{base}-app.{namespace}",
    "scheduling": {"node_pool_label": "cce.cloud.com/cce-nodepool", "node_pool": "", "anti_affinity": True,
                   "zone_spread": True},
    "image_pull_secret": "default-secret",
    "log": {"enabled": False, "host_path": "", "mount_path": "/applog", "sub_path_expr": ""},
    "defaults": {"replicas": 1, "cpu_request": "500m", "memory_request": "4Gi", "cpu_limit": "2", "memory_limit": "4Gi"},
}
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
_ABSOLUTE_PATH = re.compile(r"/[^\0]{0,1023}")
RESERVED_INGRESS_ANNOTATIONS = {
    "kubernetes.io/elb.class", "kubernetes.io/elb.id", "kubernetes.io/elb.port", "kubernetes.io/elb.tls-certificate-ids",
    # 由 CCE 维护
    "kubernetes.io/elb.listener-master-ingress", "kubernetes.io/elb.inconsistent-listener-config",
}
_ELB_ID = re.compile(r"[A-Za-z0-9-]{1,64}")


# ------------------------------------------------------------------ 档案校验
def _template(value: Any, field: str, allowed: tuple, pattern: re.Pattern, limit: int, sample: Dict[str, str],
              allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是字符串")
    value = value.strip()
    if not value and allow_empty:
        return ""
    unknown = set(_PLACEHOLDER.findall(value)) - set(allowed)
    if unknown:
        raise ValueError(f"{field} 只能使用占位符 {', '.join('{' + a + '}' for a in allowed)}")
    try:
        rendered = value.format(**sample)
    except (KeyError, IndexError, ValueError):
        raise ValueError(f"{field} 的占位符格式错误") from None
    if len(rendered) > limit or not pattern.fullmatch(rendered):
        raise ValueError(f"{field} 替换后不是合法的 Kubernetes 名称（示例：{rendered}）")
    return value


def merged_profile(profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """在默认值上叠加已保存的档案（档案缺少的字段使用默认值）。"""
    result = copy.deepcopy(DEFAULT_PROFILE)
    for key, value in (profile or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key].update(value)
        else:
            result[key] = value
    return result


def normalize_profile(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("环境档案必须是对象")
    reject_unknown(data, tuple(DEFAULT_PROFILE), "环境档案包含不支持的字段")
    profile = merged_profile(data)
    sample = {"base": "order-api", "service": "order-api", "namespace": "demo-sit"}
    naming = profile["naming"]
    if not isinstance(naming, dict):
        raise ValueError("naming 必须是对象")
    reject_unknown(naming, NAMING_KEYS, "naming 包含不支持的对象类型")
    for key in NAMING_KEYS:
        # Service 名称是 DNS-1035 label（≤63），其余对象允许 DNS 子域名
        pattern, limit = (DNS_LABEL_RE, 63) if key == "service" else (DNS_SUBDOMAIN_RE, 253)
        naming[key] = _template(naming[key], f"naming.{key}", ("base", "service", "namespace"), pattern, limit, sample)
    profile["host"] = _template(profile["host"], "host", ("base", "service", "namespace"), DNS_SUBDOMAIN_RE, 253, sample,
                                allow_empty=True)
    scheduling = profile["scheduling"]
    reject_unknown(scheduling, tuple(DEFAULT_PROFILE["scheduling"]), "scheduling 包含不支持的字段")
    if not isinstance(scheduling["node_pool_label"], str) or not LABEL_KEY_RE.fullmatch(scheduling["node_pool_label"]):
        raise ValueError("scheduling.node_pool_label 必须是合法的标签键")
    if not isinstance(scheduling["node_pool"], str) or not LABEL_VALUE_RE.fullmatch(scheduling["node_pool"]):
        raise ValueError("scheduling.node_pool 必须是合法的标签值（可以为空，表示不限节点池）")
    for key in ("anti_affinity", "zone_spread"):
        if not isinstance(scheduling[key], bool):
            raise ValueError(f"scheduling.{key} 必须是布尔值")
    secret = profile["image_pull_secret"]
    if not isinstance(secret, str) or (secret and not DNS_SUBDOMAIN_RE.fullmatch(secret)):
        raise ValueError("image_pull_secret 必须是 Secret 名称或为空")
    log = profile["log"]
    reject_unknown(log, tuple(DEFAULT_PROFILE["log"]), "log 包含不支持的字段")
    if not isinstance(log["enabled"], bool):
        raise ValueError("log.enabled 必须是布尔值")
    if log["enabled"]:
        for key in ("host_path", "mount_path"):
            if not isinstance(log[key], str) or not _ABSOLUTE_PATH.fullmatch(log[key]):
                raise ValueError(f"log.{key} 必须是绝对路径")
    if not isinstance(log["sub_path_expr"], str) or len(log["sub_path_expr"]) > 500:
        raise ValueError("log.sub_path_expr 最多 500 字符")
    defaults = profile["defaults"]
    reject_unknown(defaults, tuple(DEFAULT_PROFILE["defaults"]), "defaults 包含不支持的字段")
    replicas = defaults["replicas"]
    if not isinstance(replicas, int) or isinstance(replicas, bool) or not 0 <= replicas <= 100:
        raise ValueError("defaults.replicas 必须是 0–100 的整数")
    for key in ("cpu_request", "memory_request", "cpu_limit", "memory_limit"):
        if not isinstance(defaults[key], str) or not QUANTITY_RE.fullmatch(defaults[key]):
            raise ValueError(f"defaults.{key} 必须是 Kubernetes 资源数量，例如 500m、2、4Gi")
    return profile


def normalize_ingress_spec(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("spec 必须是对象")
    reject_unknown(data, ("ingress_class", "elb_class", "elb_id", "port", "certificate_ids", "annotations"),
                   "spec 包含不支持的字段")
    ingress_class = data.get("ingress_class", "cce")
    if not isinstance(ingress_class, str) or not DNS_LABEL_RE.fullmatch(ingress_class):
        raise ValueError("ingress_class 必须是 IngressClass 名称，例如 cce")
    elb_class = data.get("elb_class", "performance")
    if not isinstance(elb_class, str) or not re.fullmatch(r"[a-z]{1,32}", elb_class):
        raise ValueError("elb_class 必须是 ELB 类型，例如 performance（独享型）或 union（共享型）")
    elb_id = data.get("elb_id", "")
    if not isinstance(elb_id, str) or not _ELB_ID.fullmatch(elb_id):
        raise ValueError("elb_id 必须是 ELB 实例 ID")
    port = data.get("port", 443)
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValueError("port 必须是 1–65535 的端口")
    certificate_ids = data.get("certificate_ids", [])
    if (not isinstance(certificate_ids, list) or len(certificate_ids) > 20
            or any(not isinstance(c, str) or not _ELB_ID.fullmatch(c) for c in certificate_ids)):
        raise ValueError("certificate_ids 必须是 ELB 证书 ID 列表（最多 20 个，第一个为服务器证书）")
    annotations = data.get("annotations", {})
    if not isinstance(annotations, dict) or len(annotations) > 30:
        raise ValueError("annotations 必须是对象，最多 30 项")
    for key, value in annotations.items():
        if not isinstance(key, str) or not LABEL_KEY_RE.fullmatch(key) or not isinstance(value, str) or len(value) > 4096:
            raise ValueError(f"注解 {key} 无效")
        if key in RESERVED_INGRESS_ANNOTATIONS:
            raise ValueError(f"注解 {key} 由平台或 CCE 维护，不能手工设置")
    return {"ingress_class": ingress_class, "elb_class": elb_class, "elb_id": elb_id, "port": port,
            "certificate_ids": certificate_ids, "annotations": dict(annotations)}


# ------------------------------------------------------------------ 负载均衡入口
def can_list_ingress_profiles(me: Principal) -> bool:
    return me.can("certificate.view") or me.can_anywhere("environment.config")


def list_ingress_profiles(store: Store, me: Principal) -> List[Dict[str, Any]]:
    if not can_list_ingress_profiles(me):
        raise Forbidden("没有查看负载均衡入口的权限")
    return store.list_ingress_profiles()


def _ingress_body(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("请求体必须是对象")
    reject_unknown(data, ("name", "description", "spec", "base_version"), "包含不支持的字段")
    name = text_field(data, "name", 80)
    if not name:
        raise ValueError("请填写名称")
    return {"name": name, "description": text_field(data, "description", 300),
            "spec": normalize_ingress_spec(data.get("spec", {}))}


def create_ingress_profile(store: Store, actor_id: int, data: Any) -> Dict[str, Any]:
    body = _ingress_body(data)
    profile_id = store.create_ingress_profile(body["name"], body["description"], body["spec"], actor_id)
    return store.get_ingress_profile(profile_id)  # type: ignore[return-value]


def update_ingress_profile(store: Store, actor_id: int, profile_id: int, data: Any) -> Dict[str, Any]:
    body = _ingress_body(data)
    base_version = data.get("base_version")
    if not isinstance(base_version, int) or isinstance(base_version, bool):
        raise ValueError("base_version 必须是整数")
    if not store.update_ingress_profile(profile_id, base_version, body["name"], body["description"], body["spec"],
                                        actor_id):
        raise NotFound("负载均衡入口不存在")
    return store.get_ingress_profile(profile_id)  # type: ignore[return-value]


def delete_ingress_profile(store: Store, profile_id: int) -> None:
    if not store.delete_ingress_profile(profile_id):
        raise NotFound("负载均衡入口不存在")


# ------------------------------------------------------------------ 环境
def _summary(store: Store, me: Principal, namespace: Dict[str, Any], profile_row: Optional[Dict[str, Any]],
             ingress_names: Dict[int, str], service_counts: Dict[int, int]) -> Dict[str, Any]:
    ingress_id = profile_row["ingress_profile_id"] if profile_row else None
    return {
        "id": namespace["id"], "name": namespace["name"], "cluster_id": namespace["cluster_id"],
        "cluster_name": namespace.get("cluster_name"), "tier": namespace["tier"], "project_ids": namespace["project_ids"],
        "profile_version": profile_row["version"] if profile_row else 0,
        "ingress_profile_id": ingress_id, "ingress_profile_name": ingress_names.get(ingress_id) if ingress_id else None,
        "service_count": service_counts.get(namespace["id"], 0),
        "can_edit": me.can_namespace(namespace["id"], "environment.config"),
    }


def list_environments(store: Store, me: Principal) -> List[Dict[str, Any]]:
    namespaces = store.list_namespaces(me.namespace_ids("namespace.view"))
    profiles = store.list_environment_profiles()
    ingress_names = {p["id"]: p["name"] for p in store.list_ingress_profiles()}
    counts: Dict[int, int] = {}
    if namespaces:
        for service in store.list_services(namespace_ids=[n["id"] for n in namespaces]):
            counts[service["namespace_id"]] = counts.get(service["namespace_id"], 0) + 1
    return [_summary(store, me, n, profiles.get(n["id"]), ingress_names, counts) for n in namespaces]


def get_environment(store: Store, me: Principal, namespace_id: int) -> Dict[str, Any]:
    namespace = namespace_or_404(store, me, namespace_id)
    row = store.get_environment_profile(namespace_id)
    ingress = store.get_ingress_profile(row["ingress_profile_id"]) if row and row["ingress_profile_id"] else None
    services = store.list_services(namespace_ids=[namespace_id])
    summary = _summary(store, me, namespace, row, {ingress["id"]: ingress["name"]} if ingress else {},
                       {namespace_id: len(services)})
    summary.update(
        policy=namespace["policy"], profile=merged_profile(row["profile"] if row else None),
        profile_saved=bool(row), ingress_profile=ingress,
        config_sets=[configsets.public_config_set(store, me, item)
                     for item in store.list_config_sets([("environment", namespace_id)])],
        can_reveal=me.can_namespace(namespace_id, "secret.view"),
    )
    return summary


def save_profile(store: Store, me: Principal, namespace_id: int, data: Any) -> Dict[str, Any]:
    namespace_or_404(store, me, namespace_id)
    if not me.can_namespace(namespace_id, "environment.config"):
        raise Forbidden("没有修改该环境的权限")
    if not isinstance(data, dict):
        raise ValueError("请求体必须是对象")
    reject_unknown(data, ("base_version", "profile", "ingress_profile_id"), "包含不支持的字段")
    base_version = data.get("base_version")
    if not isinstance(base_version, int) or isinstance(base_version, bool):
        raise ValueError("base_version 必须是整数（首次保存为 0）")
    profile = normalize_profile(data.get("profile", {}))
    ingress_id = data.get("ingress_profile_id")
    if ingress_id is not None:
        if not isinstance(ingress_id, int) or isinstance(ingress_id, bool) or not store.get_ingress_profile(ingress_id):
            raise ValueError("负载均衡入口不存在")
    store.save_environment_profile(namespace_id, base_version, profile, ingress_id, me.id)
    return get_environment(store, me, namespace_id)
