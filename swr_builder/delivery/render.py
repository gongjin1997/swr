"""渲染：托管服务的输入 → Kubernetes 对象清单（纯函数，不访问数据库和集群）。

对象按下发顺序排列：共享配置（公共配置文件、证书目录）→ 服务自己的 ConfigMap / Secret → Service →
Deployment → Ingress → HPA。所有对象带 `app.kubernetes.io/managed-by: swr-builder`；服务的对象另带
`swr-builder/service: <服务 ID>`，共享配置另带 `swr-builder/config-set: <配置集 ID>` 与版本注解。

Pod 模板注解 `swr-builder/config-hash` 是所引用配置版本的摘要：环境变量（envFrom）与 subPath 挂载的文件
在 ConfigMap 更新后都不会自动生效，配置变化时注解随之变化，触发滚动更新。
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

from .spec import (DOWNWARD_ENV, PROBE_TIMINGS, SECURITY_CONTEXT, effective_resources, ingress_host, object_names,
                   selector_of)

MANAGED_BY_LABEL = "app.kubernetes.io/managed-by"
MANAGED_BY = "swr-builder"
SERVICE_LABEL = "swr-builder/service"
CONFIG_SET_LABEL = "swr-builder/config-set"
CONFIG_VERSION_ANNOTATION = "swr-builder/config-version"
CONFIG_HASH_ANNOTATION = "swr-builder/config-hash"
DEPLOYMENT_ANNOTATION = "swr-builder/deployment-id"
REVISION_ANNOTATION = "swr-builder/revision"
URL_MATCH_PROPERTY = "ingress.beta.kubernetes.io/url-match-mode"
ROLLING_UPDATE = {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": "25%", "maxUnavailable": "25%"}}
API_VERSIONS = {"ConfigMap": "v1", "Secret": "v1", "Service": "v1", "Deployment": "apps/v1",
                "Ingress": "networking.k8s.io/v1", "HorizontalPodAutoscaler": "autoscaling/v2"}


@dataclass
class ConfigObject:
    """一个配置集（或合并后的环境变量）在集群中对应的 ConfigMap 或 Secret。"""
    name: str
    secret: bool
    data: Dict[str, bytes]
    shared: bool
    source: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RenderInputs:
    service_id: int
    service_name: str
    namespace: str
    spec: Dict[str, Any]
    profile: Dict[str, Any]
    ingress: Optional[Dict[str, Any]]
    image: str
    env: Dict[str, str]
    secret_env: Dict[str, bytes]
    mounts: Dict[str, ConfigObject]
    config_hash: str
    revision_id: Optional[int] = None
    deployment_id: Optional[int] = None
    warnings: List[str] = field(default_factory=list)


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _metadata(name: str, namespace: str, labels: Dict[str, str],
              annotations: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    meta: Dict[str, Any] = {"name": name, "namespace": namespace, "labels": dict(labels)}
    if annotations:
        meta["annotations"] = dict(annotations)
    return meta


def _config_object(obj: ConfigObject, namespace: str, labels: Dict[str, str]) -> Dict[str, Any]:
    annotations = {}
    if obj.source.get("set_id"):
        labels = {**labels, CONFIG_SET_LABEL: str(obj.source["set_id"])}
        annotations[CONFIG_VERSION_ANNOTATION] = str(obj.source.get("version", ""))
    item: Dict[str, Any] = {"apiVersion": "v1", "kind": "Secret" if obj.secret else "ConfigMap",
                            "metadata": _metadata(obj.name, namespace, labels, annotations)}
    if obj.secret:
        item["type"] = "Opaque"
        item["data"] = {key: _b64(value) for key, value in sorted(obj.data.items())}
    else:
        item["data"] = {key: value.decode("utf-8") for key, value in sorted(obj.data.items())}
    return item


def _probe(spec: Dict[str, Any], kind: str) -> Dict[str, Any]:
    probe = spec["probe"]
    if probe["type"] == "tcp":
        action: Dict[str, Any] = {"tcpSocket": {"port": probe["port"]}}
    else:
        action = {"httpGet": {"path": probe["path"], "port": probe["port"], "scheme": probe["scheme"]}}
    return {**action, **PROBE_TIMINGS[kind]}


def _affinity(profile: Dict[str, Any], selector: Dict[str, str]) -> Dict[str, Any]:
    scheduling = profile.get("scheduling") or {}
    affinity: Dict[str, Any] = {}
    if scheduling.get("node_pool"):
        affinity["nodeAffinity"] = {"requiredDuringSchedulingIgnoredDuringExecution": {"nodeSelectorTerms": [
            {"matchExpressions": [{"key": scheduling["node_pool_label"], "operator": "In",
                                   "values": [scheduling["node_pool"]]}]}]}}
    if scheduling.get("anti_affinity"):
        affinity["podAntiAffinity"] = {"requiredDuringSchedulingIgnoredDuringExecution": [{
            "labelSelector": {"matchExpressions": [{"key": k, "operator": "In", "values": [v]}
                                                   for k, v in sorted(selector.items())]},
            "topologyKey": "kubernetes.io/hostname"}]}
    return affinity


def render(inputs: RenderInputs) -> List[Dict[str, Any]]:
    """生成对象清单。规格必须已通过 normalize_managed_spec 校验。"""
    spec, profile, namespace = inputs.spec, inputs.profile, inputs.namespace
    names = object_names(spec, inputs.service_name, namespace, profile)
    selector = selector_of(spec, names)
    labels = {MANAGED_BY_LABEL: MANAGED_BY, SERVICE_LABEL: str(inputs.service_id)}
    objects: List[Dict[str, Any]] = []

    shared = sorted((o for o in inputs.mounts.values() if o.shared), key=lambda o: o.name)
    for obj in shared:
        objects.append(_config_object(obj, namespace, {MANAGED_BY_LABEL: MANAGED_BY}))
    objects.append({"apiVersion": "v1", "kind": "ConfigMap", "metadata": _metadata(names["configmap"], namespace, labels),
                    "data": dict(sorted(inputs.env.items()))})
    objects.append({"apiVersion": "v1", "kind": "Secret", "metadata": _metadata(names["secret"], namespace, labels),
                    "type": "Opaque", "data": {k: _b64(v) for k, v in sorted(inputs.secret_env.items())}})
    for obj in sorted((o for o in inputs.mounts.values() if not o.shared), key=lambda o: o.name):
        objects.append(_config_object(obj, namespace, labels))

    service = spec["service"]
    if service["enabled"]:
        ports = []
        for port in service["ports"]:
            item: Dict[str, Any] = {"port": port["port"], "targetPort": port.get("target_port", port["port"]),
                                    "protocol": "TCP"}
            if port.get("name"):
                item = {"name": port["name"], **item}
            if port.get("node_port"):
                item["nodePort"] = port["node_port"]
            ports.append(item)
        objects.append({"apiVersion": "v1", "kind": "Service",
                        "metadata": _metadata(names["service"], namespace, {**labels, **selector}),
                        "spec": {"type": service["type"], "selector": selector, "ports": ports}})

    container_spec = spec["container"]
    env: List[Dict[str, Any]] = [{"name": e["name"], "value": e["value"]} for e in container_spec["env"]]
    if container_spec["downward_api"]:
        env += [{"name": name, "valueFrom": {"fieldRef": {"apiVersion": "v1", "fieldPath": path}}}
                for name, path in DOWNWARD_ENV]
    resources = effective_resources(spec, profile)
    container: Dict[str, Any] = {
        "name": names["container"], "image": inputs.image, "imagePullPolicy": container_spec["image_pull_policy"],
        "envFrom": [{"configMapRef": {"name": names["configmap"]}}, {"secretRef": {"name": names["secret"]}}],
        "resources": {"requests": {k: resources[f"{k}_request"] for k in ("cpu", "memory") if resources[f"{k}_request"]},
                      "limits": {k: resources[f"{k}_limit"] for k in ("cpu", "memory") if resources[f"{k}_limit"]}},
        "securityContext": dict(SECURITY_CONTEXT),
    }
    if env:
        container["env"] = env
    if spec["probe"]["type"] != "none":
        for kind in ("startupProbe", "readinessProbe", "livenessProbe"):
            container[kind] = _probe(spec, kind)
    volumes: List[Dict[str, Any]] = []
    mounts: List[Dict[str, Any]] = []
    for mount in spec["mounts"]:
        obj = inputs.mounts[mount["config"]]
        source = ({"secret": {"secretName": obj.name, "defaultMode": 420}} if obj.secret
                  else {"configMap": {"name": obj.name, "defaultMode": 420}})
        volumes.append({"name": mount["volume"], **source})
        if mount["items"]:
            mounts += [{"name": mount["volume"], "mountPath": f"{mount['path'].rstrip('/')}/{item}", "subPath": item}
                       for item in mount["items"]]
        else:
            mounts.append({"name": mount["volume"], "mountPath": mount["path"]})
    log = profile.get("log") or {}
    if spec["log"]["enabled"] and log.get("enabled"):
        volumes.append({"name": spec["log"]["volume"], "hostPath": {"path": log["host_path"]}})
        mount = {"name": spec["log"]["volume"], "mountPath": log["mount_path"]}
        if log.get("sub_path_expr"):
            mount["subPathExpr"] = log["sub_path_expr"]
        mounts.append(mount)
    if mounts:
        container["volumeMounts"] = mounts
    annotations = dict(sorted(spec["pod_annotations"].items()))
    annotations[CONFIG_HASH_ANNOTATION] = inputs.config_hash
    if inputs.revision_id is not None:
        annotations[REVISION_ANNOTATION] = str(inputs.revision_id)
    if inputs.deployment_id is not None:
        annotations[DEPLOYMENT_ANNOTATION] = str(inputs.deployment_id)
    pod_spec: Dict[str, Any] = {"containers": [container], "automountServiceAccountToken": False,
                                "terminationGracePeriodSeconds": spec["workload"]["termination_grace_period_seconds"]}
    if volumes:
        pod_spec["volumes"] = volumes
    if profile.get("image_pull_secret"):
        pod_spec["imagePullSecrets"] = [{"name": profile["image_pull_secret"]}]
    affinity = _affinity(profile, selector)
    if affinity:
        pod_spec["affinity"] = affinity
    if (profile.get("scheduling") or {}).get("zone_spread"):
        pod_spec["topologySpreadConstraints"] = [{"maxSkew": 1, "topologyKey": "topology.kubernetes.io/zone",
                                                  "whenUnsatisfiable": "DoNotSchedule",
                                                  "labelSelector": {"matchLabels": dict(selector)}}]
    deployment: Dict[str, Any] = {
        "apiVersion": "apps/v1", "kind": "Deployment", "metadata": _metadata(names["deployment"], namespace, labels),
        "spec": {"selector": {"matchLabels": dict(selector)}, "strategy": copy.deepcopy(ROLLING_UPDATE),
                 "revisionHistoryLimit": 10, "progressDeadlineSeconds": 600,
                 "template": {"metadata": {"labels": dict(selector), "annotations": annotations}, "spec": pod_spec}},
    }
    if not spec["hpa"]["enabled"]:
        replicas = spec["replicas"]
        deployment["spec"]["replicas"] = replicas if replicas is not None else (profile.get("defaults") or {}).get("replicas", 1)
    objects.append(deployment)

    ingress = spec["ingress"]
    if ingress["enabled"]:
        if not inputs.ingress:
            raise ValueError("环境没有配置负载均衡入口，不能启用 Ingress（在环境的“入口与调度”中选择）")
        entry = inputs.ingress
        port = ingress["port"] or service["ports"][0]["port"]
        ingress_annotations = {"kubernetes.io/elb.class": entry["elb_class"], "kubernetes.io/elb.id": entry["elb_id"],
                               "kubernetes.io/elb.port": str(entry["port"]), **(entry.get("annotations") or {})}
        if entry.get("certificate_ids"):
            ingress_annotations["kubernetes.io/elb.tls-certificate-ids"] = ",".join(entry["certificate_ids"])
        path: Dict[str, Any] = {"path": ingress["path"], "pathType": "Prefix",
                                "backend": {"service": {"name": names["service"], "port": {"number": port}}}}
        mode = ingress.get("url_match_mode") or entry.get("url_match_mode") or ""
        if mode:
            path["property"] = {URL_MATCH_PROPERTY: mode}
        rule: Dict[str, Any] = {"http": {"paths": [path]}}
        host = ingress_host(spec, inputs.service_name, namespace, profile)
        if host:
            rule = {"host": host, **rule}
        objects.append({"apiVersion": "networking.k8s.io/v1", "kind": "Ingress",
                        "metadata": _metadata(names["ingress"], namespace, labels, dict(sorted(ingress_annotations.items()))),
                        "spec": {"ingressClassName": entry["ingress_class"], "rules": [rule]}})

    hpa = spec["hpa"]
    if hpa["enabled"]:
        objects.append({"apiVersion": "autoscaling/v2", "kind": "HorizontalPodAutoscaler",
                        "metadata": _metadata(names["hpa"], namespace, labels),
                        "spec": {"scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment",
                                                    "name": names["deployment"]},
                                 "minReplicas": hpa["min_replicas"], "maxReplicas": hpa["max_replicas"],
                                 "metrics": [{"type": "Resource", "resource": {"name": "cpu", "target": {
                                     "type": "Utilization", "averageUtilization": hpa["cpu_utilization"]}}}]}})
    return objects


# ------------------------------------------------------------------ 展示与快照
def mask(objects: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Secret 的值替换为长度说明，用于预览、发布快照与审计。"""
    result = []
    for obj in objects:
        item = copy.deepcopy(obj)
        if item["kind"] == "Secret":
            item["data"] = {key: f"（已隐藏，{len(base64.b64decode(value))} 字节）" for key, value in item.get("data", {}).items()}
        result.append(item)
    return result


def checksum(objects: List[Dict[str, Any]]) -> str:
    raw = json.dumps(objects, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def to_yaml(objects: List[Dict[str, Any]]) -> str:
    return yaml.safe_dump_all(objects, allow_unicode=True, sort_keys=False, default_flow_style=False, width=120)


def object_key(obj: Dict[str, Any]) -> str:
    return f"{obj['kind']}/{obj['metadata']['name']}"
