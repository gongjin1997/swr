"""托管服务规格（mode: managed）：内置模板、默认值、校验与对象命名。

规格只保存服务自身的参数；与环境强相关的部分来自环境档案（对象命名规则、主机名规则、节点池调度、
镜像拉取密钥、日志目录、资源与副本默认值，见 services/environments.py），配置值来自分层配置集。

内置模板 web 对应附件中服务的对象结构：
- Deployment：单容器，envFrom 引用服务自己的 ConfigMap 与 Secret，Downward API 注入节点 IP、Pod 名、命名空间与 Pod IP；
  启动、就绪、存活三个 HTTP(S) 健康检查；非 root 运行、禁止提权、不挂载 ServiceAccount 令牌；
  滚动更新 25% / 25%；节点池亲和、同服务反亲和、跨可用区打散由环境档案决定；
- Service、Ingress（引用环境的负载均衡入口）、HPA（CPU 利用率）均可选；
- 公共配置文件、证书目录按名称引用环境（或项目、平台）层的配置集挂载，服务自己的 properties 文件以 subPath 挂载。
"""
from __future__ import annotations

import copy
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from ..validation import (CONFIG_KEY_RE, DNS_LABEL_RE, DNS_SUBDOMAIN_RE, LABEL_KEY_RE, LABEL_VALUE_RE, QUANTITY_RE,
                          reject_unknown)

TEMPLATES: Dict[str, Dict[str, Any]] = {
    "web": {
        "code": "web", "name": "Web 服务", "version": 1,
        "description": "Deployment + Service，可选 Ingress 与 HPA；容器通过 envFrom 读取服务的 ConfigMap 与 Secret，"
                       "可挂载公共配置文件和证书目录。与现有 CCE 服务的对象结构一致。",
        "objects": ["ConfigMap（环境变量）", "Secret（敏感变量）", "ConfigMap（服务配置文件）", "Service", "Deployment",
                    "Ingress", "HorizontalPodAutoscaler"],
    },
}
NAME_ROLES = ("deployment", "service", "ingress", "configmap", "secret", "properties")
# spec.names 可以覆盖的对象：环境档案的命名规则 + HPA（默认与 Deployment 同名）
OVERRIDE_ROLES = NAME_ROLES + ("hpa",)
# 服务层配置文件集的固定名称，渲染为环境档案命名规则中的 properties 对象（默认 {base}-properties）
SERVICE_FILES = "properties"
DOWNWARD_ENV = (("HOST_IP", "status.hostIP"), ("POD_NAME", "metadata.name"), ("POD_NAMESPACE", "metadata.namespace"),
                ("POD_IP", "status.podIP"))
PROBE_TIMINGS: Dict[str, Dict[str, int]] = {
    "startupProbe": {"periodSeconds": 6, "timeoutSeconds": 5, "failureThreshold": 30, "successThreshold": 1},
    "readinessProbe": {"periodSeconds": 15, "timeoutSeconds": 5, "failureThreshold": 3, "successThreshold": 1},
    "livenessProbe": {"periodSeconds": 30, "timeoutSeconds": 5, "failureThreshold": 3, "successThreshold": 1},
}
SECURITY_CONTEXT = {"runAsUser": 65532, "runAsGroup": 65532, "runAsNonRoot": True, "allowPrivilegeEscalation": False}
RESOURCE_KEYS = ("cpu_request", "memory_request", "cpu_limit", "memory_limit")
PULL_POLICIES = ("Always", "IfNotPresent", "Never")
PROBE_TYPES = ("http", "tcp", "none")
URL_MATCH_MODES = ("", "STARTS_WITH", "EQUAL_TO", "REGEX")
RESERVED_ANNOTATION_PREFIX = "swr-builder/"
MAX_ENV, MAX_MOUNTS, MAX_PORTS, MAX_ANNOTATIONS = 200, 20, 10, 30

DEFAULT_SPEC: Dict[str, Any] = {
    "mode": "managed",
    "template": "web",
    # 资源基础名，默认等于服务名；对象名按环境档案的命名规则由它生成
    "base": "",
    # 个别对象名的覆盖（导入命名不规范的已有服务时使用）
    "names": {},
    # Deployment 的 selector（创建后不可修改）；为空时为 {app: <Deployment 名>}
    "selector": {},
    "container": {"name": "", "env": [], "downward_api": True, "image_pull_policy": "IfNotPresent"},
    "probe": {"type": "http", "scheme": "HTTP", "path": "/actuator/health", "port": 8080},
    # 为空的项使用环境档案中的默认值
    "resources": {},
    "replicas": None,
    "workload": {"termination_grace_period_seconds": 30},
    "service": {"enabled": True, "type": "ClusterIP", "ports": [{"name": "http", "port": 8080, "target_port": 8080}]},
    # host 为空时按环境档案的主机名规则生成；port 为空时转发到 Service 的第一个端口
    "ingress": {"enabled": False, "host": "", "path": "/", "port": None, "url_match_mode": ""},
    "hpa": {"enabled": False, "min_replicas": 1, "max_replicas": 4, "cpu_utilization": 70},
    # [{config, path, items, volume}]：items 为空时把整个配置挂载为目录，否则逐个文件以 subPath 挂载到 path/<文件名>
    "mounts": [],
    # 使用环境档案中的日志目录（hostPath + subPathExpr）
    "log": {"enabled": False, "volume": "log"},
    "pod_annotations": {},
}
SPEC_KEYS = tuple(DEFAULT_SPEC)
_NESTED = ("container", "probe", "workload", "service", "ingress", "hpa", "log")
ENV_NAME_RE = re.compile(r"[-._a-zA-Z][-._a-zA-Z0-9]{0,252}")
PATH_RE = re.compile(r"/[^\0]{0,1023}")
IANA_NAME_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,13}[a-z0-9])?")
_QUANTITY_UNITS = {"Ki": 2 ** 10, "Mi": 2 ** 20, "Gi": 2 ** 30, "Ti": 2 ** 40, "Pi": 2 ** 50, "Ei": 2 ** 60,
                   "k": 10 ** 3, "M": 10 ** 6, "G": 10 ** 9, "T": 10 ** 12, "P": 10 ** 15, "E": 10 ** 18}


def parse_quantity(value: str) -> Decimal:
    """Kubernetes 资源数量 → 数值（CPU 为核数，内存为字节）。"""
    text = str(value).strip()
    try:
        for suffix in sorted(_QUANTITY_UNITS, key=len, reverse=True):
            if text.endswith(suffix):
                return Decimal(text[:-len(suffix)]) * _QUANTITY_UNITS[suffix]
        if text.endswith("m"):
            return Decimal(text[:-1]) / 1000
        return Decimal(text)
    except InvalidOperation:
        raise ValueError(f"资源数量 {value!r} 无效") from None


def default_spec(**overrides: Any) -> Dict[str, Any]:
    spec = copy.deepcopy(DEFAULT_SPEC)
    spec.update(overrides)
    return spec


def _merged(value: Dict[str, Any]) -> Dict[str, Any]:
    spec = copy.deepcopy(DEFAULT_SPEC)
    for key, item in value.items():
        if key in _NESTED and isinstance(item, dict) and isinstance(spec[key], dict):
            spec[key] = {**spec[key], **item}
        else:
            spec[key] = copy.deepcopy(item)
    return spec


def _int(value: Any, field: str, low: int, high: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ValueError(f"{field} 必须是 {low}–{high} 的整数")
    return value


def _bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} 必须是布尔值")
    return value


def volume_name(config: str) -> str:
    """配置名 → 默认卷名（DNS label）。"""
    name = re.sub(r"[^a-z0-9-]", "-", config.lower())[:63].strip("-")
    return name or "config"


def normalize_managed_spec(value: Any, namespace: Dict[str, Any]) -> Dict[str, Any]:
    """校验托管服务规格并补齐默认值。只检查与环境档案无关的规则；依赖环境档案的检查在渲染时进行（resolve.check_render）。"""
    from ..services.namespaces import effective_policy

    if not isinstance(value, dict):
        raise ValueError("托管服务规格必须是对象")
    reject_unknown(value, SPEC_KEYS, "托管服务规格包含不支持的字段：" + "、".join(sorted(set(value) - set(SPEC_KEYS))))
    spec = _merged(value)
    policy = effective_policy(namespace["policy"])
    if spec["mode"] != "managed" or spec["template"] not in TEMPLATES:
        raise ValueError("模板不存在")
    base = spec["base"]
    if not isinstance(base, str) or (base and (len(base) > 50 or not DNS_LABEL_RE.fullmatch(base))):
        raise ValueError("base 必须为空或不超过 50 个字符的 DNS 名称（小写字母、数字、-）")
    names = spec["names"]
    if not isinstance(names, dict):
        raise ValueError("names 必须是对象")
    reject_unknown(names, OVERRIDE_ROLES, "names 只能覆盖 " + "、".join(OVERRIDE_ROLES))
    for role, name in list(names.items()):
        pattern, limit = (DNS_LABEL_RE, 63) if role == "service" else (DNS_SUBDOMAIN_RE, 253)
        if name in ("", None):
            names.pop(role)
        elif not isinstance(name, str) or len(name) > limit or not pattern.fullmatch(name):
            raise ValueError(f"names.{role} 不是合法的 Kubernetes 名称")
    selector = spec["selector"]
    if not isinstance(selector, dict) or len(selector) > 10:
        raise ValueError("selector 必须是最多 10 项的标签对象")
    for key, item in selector.items():
        if not isinstance(key, str) or not LABEL_KEY_RE.fullmatch(key) or not isinstance(item, str) \
                or not item or not LABEL_VALUE_RE.fullmatch(item):
            raise ValueError(f"selector 标签 {key} 无效")

    container = spec["container"]
    reject_unknown(container, tuple(DEFAULT_SPEC["container"]), "container 包含不支持的字段")
    if not isinstance(container["name"], str) or (container["name"] and not DNS_LABEL_RE.fullmatch(container["name"])):
        raise ValueError("容器名必须为空（与 Deployment 同名）或 DNS 名称")
    env = container["env"]
    if not isinstance(env, list) or len(env) > MAX_ENV:
        raise ValueError(f"container.env 最多 {MAX_ENV} 项")
    seen = set()
    for item in env:
        if not isinstance(item, dict) or set(item) != {"name", "value"} or not isinstance(item["value"], str) \
                or len(item["value"]) > 32768:
            raise ValueError("container.env 的每一项必须是 {name, value}，value 最长 32768 字符")
        if not isinstance(item["name"], str) or not ENV_NAME_RE.fullmatch(item["name"]) or item["name"] in seen:
            raise ValueError(f"环境变量名 {item.get('name')!r} 无效或重复")
        seen.add(item["name"])
    _bool(container["downward_api"], "container.downward_api")
    if container["downward_api"] and seen & {name for name, _ in DOWNWARD_ENV}:
        raise ValueError("container.env 与 Downward API 注入的变量重名")
    if container["image_pull_policy"] not in PULL_POLICIES:
        raise ValueError("image_pull_policy 必须是 Always、IfNotPresent 或 Never")

    probe = spec["probe"]
    reject_unknown(probe, tuple(DEFAULT_SPEC["probe"]), "probe 包含不支持的字段")
    if probe["type"] not in PROBE_TYPES:
        raise ValueError("probe.type 必须是 http、tcp 或 none")
    if probe["type"] == "none" and policy.get("require_readiness_probe", True):
        raise ValueError("命名空间策略要求配置就绪检查")
    if probe["scheme"] not in ("HTTP", "HTTPS"):
        raise ValueError("probe.scheme 必须是 HTTP 或 HTTPS")
    if not isinstance(probe["path"], str) or not PATH_RE.fullmatch(probe["path"]):
        raise ValueError("probe.path 必须以 / 开头")
    _int(probe["port"], "probe.port", 1, 65535)

    resources = spec["resources"]
    if not isinstance(resources, dict):
        raise ValueError("resources 必须是对象")
    reject_unknown(resources, RESOURCE_KEYS, "resources 只能包含 " + "、".join(RESOURCE_KEYS))
    for key, item in list(resources.items()):
        if item in ("", None):
            resources.pop(key)
        elif not isinstance(item, str) or not QUANTITY_RE.fullmatch(item):
            raise ValueError(f"resources.{key} 必须是 Kubernetes 资源数量，例如 500m、2、4Gi")
    if spec["replicas"] is not None:
        _int(spec["replicas"], "副本数（受命名空间策略上限约束）", 0, policy["max_replicas"])
    workload = spec["workload"]
    reject_unknown(workload, tuple(DEFAULT_SPEC["workload"]), "workload 包含不支持的字段")
    _int(workload["termination_grace_period_seconds"], "workload.termination_grace_period_seconds", 0, 3600)

    service = spec["service"]
    reject_unknown(service, tuple(DEFAULT_SPEC["service"]), "service 包含不支持的字段")
    _bool(service["enabled"], "service.enabled")
    ports = service["ports"]
    if service["enabled"]:
        if service["type"] not in policy["service_types"]:
            raise ValueError(f"命名空间策略不允许 {service['type']} 类型的 Service（允许：{'、'.join(policy['service_types'])}）")
        if not isinstance(ports, list) or not 1 <= len(ports) <= MAX_PORTS:
            raise ValueError(f"Service 需要 1–{MAX_PORTS} 个端口")
        numbers = set()
        for port in ports:
            if not isinstance(port, dict):
                raise ValueError("Service 端口必须是对象")
            reject_unknown(port, ("name", "port", "target_port", "node_port"), "Service 端口包含不支持的字段")
            port.setdefault("name", "")
            if not isinstance(port["name"], str) or (port["name"] and not DNS_LABEL_RE.fullmatch(port["name"])) \
                    or (not port["name"] and len(ports) > 1):
                raise ValueError("多个端口时每个端口都需要名称（DNS 名称）")
            number = _int(port.get("port"), "Service 端口", 1, 65535)
            if number in numbers:
                raise ValueError(f"Service 端口 {number} 重复")
            numbers.add(number)
            target = port.setdefault("target_port", number)
            if not (isinstance(target, int) and not isinstance(target, bool) and 1 <= target <= 65535) and not (
                    isinstance(target, str) and IANA_NAME_RE.fullmatch(target)):
                raise ValueError("target_port 必须是端口号或容器端口名")
            if port.get("node_port") is not None:
                if service["type"] == "ClusterIP":
                    raise ValueError("ClusterIP 类型的 Service 不能指定 node_port")
                _int(port["node_port"], "node_port", 1, 65535)
            else:
                port.pop("node_port", None)

    ingress = spec["ingress"]
    reject_unknown(ingress, tuple(DEFAULT_SPEC["ingress"]), "ingress 包含不支持的字段")
    _bool(ingress["enabled"], "ingress.enabled")
    if ingress["enabled"]:
        if not policy["ingress"].get("enabled"):
            raise ValueError("命名空间策略未启用 Ingress")
        if not service["enabled"]:
            raise ValueError("启用 Ingress 需要同时启用 Service")
        host = ingress["host"]
        if not isinstance(host, str) or (host and (len(host) > 253 or not DNS_SUBDOMAIN_RE.fullmatch(host))):
            raise ValueError("ingress.host 必须为空（按环境规则生成）或合法的域名")
        if not isinstance(ingress["path"], str) or not PATH_RE.fullmatch(ingress["path"]):
            raise ValueError("ingress.path 必须以 / 开头")
        if ingress["port"] is not None and ingress["port"] not in {p["port"] for p in ports}:
            raise ValueError("ingress.port 必须是 Service 的端口之一")
        if ingress["url_match_mode"] not in URL_MATCH_MODES:
            raise ValueError("url_match_mode 必须是 STARTS_WITH、EQUAL_TO、REGEX 或为空")

    hpa = spec["hpa"]
    reject_unknown(hpa, tuple(DEFAULT_SPEC["hpa"]), "hpa 包含不支持的字段")
    _bool(hpa["enabled"], "hpa.enabled")
    if hpa["enabled"]:
        if not policy["hpa_enabled"]:
            raise ValueError("命名空间策略未启用 HPA")
        high = _int(hpa["max_replicas"], "hpa.max_replicas", 1, policy["max_replicas"])
        _int(hpa["min_replicas"], "hpa.min_replicas", 1, high)
        _int(hpa["cpu_utilization"], "hpa.cpu_utilization", 1, 100)

    mounts = spec["mounts"]
    if not isinstance(mounts, list) or len(mounts) > MAX_MOUNTS:
        raise ValueError(f"mounts 最多 {MAX_MOUNTS} 项")
    volumes, paths = set(), set()
    for mount in mounts:
        if not isinstance(mount, dict):
            raise ValueError("mounts 的每一项必须是对象")
        reject_unknown(mount, ("config", "path", "items", "volume"), "挂载包含不支持的字段")
        config = mount.get("config")
        if not isinstance(config, str) or len(config) > 253 or not DNS_SUBDOMAIN_RE.fullmatch(config):
            raise ValueError("挂载的 config 必须是配置名称")
        path = mount.get("path")
        if not isinstance(path, str) or not PATH_RE.fullmatch(path) or path.rstrip("/") in ("", "/proc", "/sys"):
            raise ValueError(f"挂载 {config} 的路径必须是绝对路径")
        mount["path"] = path.rstrip("/") or "/"
        items = mount.setdefault("items", [])
        if not isinstance(items, list) or len(items) > 100 or len(set(items)) != len(items) \
                or any(not isinstance(i, str) or not CONFIG_KEY_RE.fullmatch(i) for i in items):
            raise ValueError(f"挂载 {config} 的文件列表无效")
        volume = mount.get("volume") or volume_name(config)
        if not DNS_LABEL_RE.fullmatch(volume):
            raise ValueError(f"卷名 {volume} 无效")
        mount["volume"] = volume
        targets = [mount["path"] + "/" + i for i in items] if items else [mount["path"]]
        if volume in volumes or paths & set(targets):
            raise ValueError(f"挂载 {config} 的卷名或路径与其他挂载重复")
        volumes.add(volume)
        paths.update(targets)
    log = spec["log"]
    reject_unknown(log, tuple(DEFAULT_SPEC["log"]), "log 包含不支持的字段")
    _bool(log["enabled"], "log.enabled")
    if not isinstance(log["volume"], str) or not DNS_LABEL_RE.fullmatch(log["volume"]) or \
            (log["enabled"] and log["volume"] in volumes):
        raise ValueError("log.volume 必须是不与其他挂载重复的卷名")
    annotations = spec["pod_annotations"]
    if not isinstance(annotations, dict) or len(annotations) > MAX_ANNOTATIONS:
        raise ValueError(f"pod_annotations 最多 {MAX_ANNOTATIONS} 项")
    for key, item in annotations.items():
        if not isinstance(key, str) or not LABEL_KEY_RE.fullmatch(key) or key.startswith(RESERVED_ANNOTATION_PREFIX) \
                or not isinstance(item, str) or len(item) > 4096:
            raise ValueError(f"Pod 注解 {key} 无效（swr-builder/ 前缀保留给平台）")
    return spec


def object_names(spec: Dict[str, Any], service_name: str, namespace_name: str,
                 profile: Dict[str, Any]) -> Dict[str, str]:
    """按环境档案的命名规则生成对象名，再应用 spec.names 的覆盖。另含容器名（container）。"""
    base = spec.get("base") or service_name
    values = {"base": base, "service": service_name, "namespace": namespace_name}
    names = {role: profile["naming"][role].format(**values) for role in NAME_ROLES}
    names["hpa"] = names["deployment"]
    names.update({k: v for k, v in (spec.get("names") or {}).items() if v})
    names["container"] = (spec.get("container") or {}).get("name") or names["deployment"][:63].rstrip("-")
    for role in OVERRIDE_ROLES:
        pattern, limit = (DNS_LABEL_RE, 63) if role == "service" else (DNS_SUBDOMAIN_RE, 253)
        if len(names[role]) > limit or not pattern.fullmatch(names[role]):
            raise ValueError(f"{role} 对象名 {names[role]} 不是合法的 Kubernetes 名称，请调整 base 或命名规则")
    if not DNS_LABEL_RE.fullmatch(names["container"]):
        raise ValueError(f"容器名 {names['container']} 无效")
    return names


def selector_of(spec: Dict[str, Any], names: Dict[str, str]) -> Dict[str, str]:
    return dict(spec.get("selector") or {"app": names["deployment"][:63].rstrip("-.")})


def ingress_host(spec: Dict[str, Any], service_name: str, namespace_name: str, profile: Dict[str, Any]) -> str:
    host = (spec.get("ingress") or {}).get("host") or ""
    if host:
        return host
    rule = profile.get("host") or ""
    return rule.format(base=spec.get("base") or service_name, service=service_name, namespace=namespace_name)


def effective_resources(spec: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, str]:
    defaults = profile.get("defaults") or {}
    return {key: (spec.get("resources") or {}).get(key) or defaults.get(key, "") for key in RESOURCE_KEYS}


def check_resources(resources: Dict[str, str], policy: Dict[str, Any]) -> List[str]:
    """资源请求与上限：上限不能小于请求，不超过命名空间策略的最大值。返回错误列表。"""
    errors: List[str] = []
    limits = policy.get("resources") or {}
    for kind in ("cpu", "memory"):
        request, limit = resources.get(f"{kind}_request"), resources.get(f"{kind}_limit")
        if limits.get("require_limits", True) and not limit:
            errors.append(f"命名空间策略要求设置 {kind} 上限")
        if request and limit and parse_quantity(request) > parse_quantity(limit):
            errors.append(f"{kind} 请求 {request} 大于上限 {limit}")
        for which, value in (("request", request), ("limit", limit)):
            maximum: Optional[str] = limits.get(f"{kind}_{which}_max")
            if value and maximum and parse_quantity(value) > parse_quantity(maximum):
                errors.append(f"{kind} {which} {value} 超过命名空间策略上限 {maximum}")
    return errors
