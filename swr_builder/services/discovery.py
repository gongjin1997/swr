"""Kubernetes 只读查询：集群发现、命名空间资源、服务运行状态。

返回值只包含展示需要的字段：不返回凭据，也不返回 Pod 或容器的环境变量值。
运行状态的判断与 kubectl 一致：Pod 状态按 `kubectl get pods` 的 STATUS 列规则计算，
Deployment 状态参照 `kubectl rollout status`。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

from ..deploy.k8s_client import KubeClient
from ..errors import DomainError
from ..store import utcnow
from .clusters import load_cluster

PLATFORM_ANNOTATIONS = ("swr-builder/deployment-id", "swr-builder/revision", "swr-builder/config-hash")
REVISION_ANNOTATION = "deployment.kubernetes.io/revision"
INCONSISTENT_ANNOTATION = "kubernetes.io/elb.inconsistent-listener-config"
ERROR_REASONS = {"CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "Error", "OOMKilled", "CreateContainerConfigError",
                 "CreateContainerError", "InvalidImageName", "Failed", "Evicted", "Unknown", "RunContainerError",
                 "StartError", "ContainerStatusUnknown", "DeadlineExceeded"}
OK_REASONS = {"Running", "Completed", "Succeeded"}
EVENT_LIMIT = 50
STATUS_CONCURRENCY = 4


# ------------------------------------------------------------------ 通用
def k8s_time(value: Any) -> Optional[str]:
    """Kubernetes 时间（RFC 3339，可能带小数秒）转换为与平台其他接口一致的 UTC ISO 字符串。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _meta(item: Dict[str, Any]) -> Dict[str, Any]:
    return item.get("metadata") or {}


def _text(value: Any, limit: int = 1000) -> str:
    return value[:limit] if isinstance(value, str) else ""


def selector_string(selector: Dict[str, Any]) -> str:
    """LabelSelector → labelSelector 查询串。"""
    parts = [f"{k}={v}" for k, v in sorted((selector.get("matchLabels") or {}).items())]
    for expression in selector.get("matchExpressions") or []:
        key, operator, values = expression.get("key", ""), expression.get("operator"), expression.get("values") or []
        if operator == "In":
            parts.append(f"{key} in ({','.join(values)})")
        elif operator == "NotIn":
            parts.append(f"{key} notin ({','.join(values)})")
        elif operator == "Exists":
            parts.append(key)
        elif operator == "DoesNotExist":
            parts.append("!" + key)
    return ",".join(parts)


def selector_matches(selector: Dict[str, Any], labels: Dict[str, str]) -> bool:
    if not selector:
        return False
    for key, value in (selector.get("matchLabels") or {}).items():
        if labels.get(key) != value:
            return False
    for expression in selector.get("matchExpressions") or []:
        key, operator, values = expression.get("key"), expression.get("operator"), expression.get("values") or []
        if operator == "In" and labels.get(key) not in values:
            return False
        if operator == "NotIn" and labels.get(key) in values:
            return False
        if operator == "Exists" and key not in labels:
            return False
        if operator == "DoesNotExist" and key in labels:
            return False
    return True


def _digest(image_id: Any) -> str:
    if isinstance(image_id, str) and "@sha256:" in image_id:
        return "sha256:" + image_id.rsplit("@sha256:", 1)[1]
    return ""


# ------------------------------------------------------------------ Pod
def pod_status(pod: Dict[str, Any]) -> Tuple[str, str]:
    """与 kubectl get pods 的 STATUS 列一致：初始化容器、等待原因、终止原因、正在删除。"""
    status, metadata = pod.get("status") or {}, _meta(pod)
    reason = status.get("reason") or status.get("phase") or "Unknown"
    message = _text(status.get("message"))
    init_total = len((pod.get("spec") or {}).get("initContainers") or [])
    initializing = False
    for index, container in enumerate(status.get("initContainerStatuses") or []):
        state = container.get("state") or {}
        terminated, waiting = state.get("terminated"), state.get("waiting")
        if terminated and terminated.get("exitCode") == 0:
            continue
        if terminated:
            detail = terminated.get("reason") or (f"Signal:{terminated['signal']}" if terminated.get("signal")
                                                  else f"ExitCode:{terminated.get('exitCode')}")
            reason, message = "Init:" + detail, _text(terminated.get("message")) or message
        elif waiting and waiting.get("reason") and waiting["reason"] != "PodInitializing":
            reason, message = "Init:" + waiting["reason"], _text(waiting.get("message")) or message
        else:
            reason = f"Init:{index}/{init_total or len(status.get('initContainerStatuses') or [])}"
        initializing = True
        break
    if not initializing:
        running = False
        for container in reversed(status.get("containerStatuses") or []):
            state = container.get("state") or {}
            waiting, terminated = state.get("waiting"), state.get("terminated")
            if waiting and waiting.get("reason"):
                reason, message = waiting["reason"], _text(waiting.get("message")) or message
            elif terminated and terminated.get("reason"):
                reason, message = terminated["reason"], _text(terminated.get("message")) or message
            elif terminated:
                reason = (f"Signal:{terminated['signal']}" if terminated.get("signal")
                          else f"ExitCode:{terminated.get('exitCode')}")
            elif container.get("ready") and state.get("running") is not None:
                running = True
        if reason == "Completed" and running:
            reason = "Running" if _pod_ready(pod) else "NotReady"
    if metadata.get("deletionTimestamp"):
        reason = "Unknown" if status.get("reason") == "NodeLost" else "Terminating"
    return reason, message


def _pod_ready(pod: Dict[str, Any]) -> bool:
    return any(c.get("type") == "Ready" and c.get("status") == "True"
               for c in (pod.get("status") or {}).get("conditions") or [])


def status_level(reason: str, ready: bool) -> str:
    """ok / warning / error，用于界面着色。"""
    if reason in ERROR_REASONS or reason.startswith(("Init:Error", "Init:CrashLoop", "Init:ImagePull", "Init:Err",
                                                     "ExitCode", "Signal", "Init:ExitCode", "Init:Signal")):
        return "error"
    if reason in OK_REASONS and (ready or reason != "Running"):
        return "ok"
    return "warning"


def summarize_container_status(container: Dict[str, Any]) -> Dict[str, Any]:
    state = container.get("state") or {}
    name = next((key for key in ("running", "waiting", "terminated") if key in state), "unknown")
    current = state.get(name) or {}
    last = (container.get("lastState") or {}).get("terminated") or {}
    return {
        "name": container.get("name", ""), "image": container.get("image", ""),
        "digest": _digest(container.get("imageID")), "ready": bool(container.get("ready")),
        "started": bool(container.get("started")), "restarts": int(container.get("restartCount") or 0),
        "state": name, "reason": _text(current.get("reason"), 200), "message": _text(current.get("message")),
        "started_at": k8s_time(current.get("startedAt")),
        "last_state": ({"reason": _text(last.get("reason"), 200), "exit_code": last.get("exitCode"),
                        "started_at": k8s_time(last.get("startedAt")), "finished_at": k8s_time(last.get("finishedAt"))}
                       if last else None),
    }


def summarize_pod(pod: Dict[str, Any]) -> Dict[str, Any]:
    metadata, spec, status = _meta(pod), pod.get("spec") or {}, pod.get("status") or {}
    containers = [summarize_container_status(c) for c in status.get("containerStatuses") or []]
    reason, message = pod_status(pod)
    ready = _pod_ready(pod)
    finished = [c["last_state"]["finished_at"] for c in containers if c["last_state"] and c["last_state"]["finished_at"]]
    last_reasons = [c["last_state"]["reason"] for c in containers if c["last_state"] and c["last_state"]["reason"]]
    total = len(spec.get("containers") or []) or len(containers)
    return {
        "name": metadata.get("name", ""), "phase": status.get("phase", ""), "status": reason, "message": message,
        "level": status_level(reason, ready), "ready": ready,
        "ready_containers": sum(1 for c in containers if c["ready"]), "total_containers": total,
        "restarts": sum(c["restarts"] for c in containers),
        "last_restart_at": max(finished) if finished else None, "last_restart_reason": last_reasons[0] if last_reasons else "",
        "created_at": k8s_time(metadata.get("creationTimestamp")), "start_time": k8s_time(status.get("startTime")),
        "node": spec.get("nodeName", ""), "pod_ip": status.get("podIP", ""), "host_ip": status.get("hostIP", ""),
        "qos_class": status.get("qosClass", ""), "labels": metadata.get("labels") or {},
        "pod_template_hash": (metadata.get("labels") or {}).get("pod-template-hash", ""),
        "deleting": bool(metadata.get("deletionTimestamp")), "containers": containers,
    }


# ------------------------------------------------------------------ Deployment / ReplicaSet
def _probe_text(probe: Optional[Dict[str, Any]]) -> str:
    if not probe:
        return ""
    if probe.get("httpGet"):
        get = probe["httpGet"]
        text = f"{get.get('scheme', 'HTTP')} GET :{get.get('port')}{get.get('path', '/')}"
    elif probe.get("tcpSocket"):
        text = f"TCP :{probe['tcpSocket'].get('port')}"
    elif probe.get("exec"):
        text = "命令"
    elif probe.get("grpc"):
        text = f"gRPC :{probe['grpc'].get('port')}"
    else:
        return "已配置"
    return f"{text}（间隔 {probe.get('periodSeconds', 10)}s，超时 {probe.get('timeoutSeconds', 1)}s，" \
           f"失败 {probe.get('failureThreshold', 3)} 次）"


def deployment_state(item: Dict[str, Any]) -> Tuple[str, str]:
    """healthy / progressing / degraded / failed / stopped / paused，以及一句说明。"""
    spec, status, metadata = item.get("spec") or {}, item.get("status") or {}, _meta(item)
    desired = spec.get("replicas", 1) if spec.get("replicas") is not None else 1
    if spec.get("paused"):
        return "paused", "Deployment 已暂停滚动"
    conditions = {c.get("type"): c for c in status.get("conditions") or []}
    progressing = conditions.get("Progressing") or {}
    if progressing.get("reason") == "ProgressDeadlineExceeded":
        return "failed", _text(progressing.get("message")) or "滚动发布超过 progressDeadlineSeconds"
    generation, observed = metadata.get("generation"), status.get("observedGeneration") or 0
    updated, total = status.get("updatedReplicas") or 0, status.get("replicas") or 0
    available = status.get("availableReplicas") or 0
    if generation is not None and observed < generation:
        return "progressing", "等待控制器处理最新规格"
    if updated < desired:
        return "progressing", f"已更新 {updated}/{desired} 个副本"
    if total > updated:
        return "progressing", f"{total - updated} 个旧副本等待终止"
    if available < updated:
        rolling = progressing.get("status") == "True" and progressing.get("reason") not in ("NewReplicaSetAvailable", None)
        return ("progressing" if rolling else "degraded"), f"可用副本 {available}/{desired}"
    if desired == 0:
        return "stopped", "副本数为 0"
    return "healthy", f"{available}/{desired} 个副本可用"


def summarize_deployment(item: Dict[str, Any]) -> Dict[str, Any]:
    metadata, spec, status = _meta(item), item.get("spec") or {}, item.get("status") or {}
    template = spec.get("template") or {}
    strategy = spec.get("strategy") or {}
    rolling = strategy.get("rollingUpdate") or {}
    state, message = deployment_state(item)
    annotations = (template.get("metadata") or {}).get("annotations") or {}
    return {
        "name": metadata.get("name", ""), "uid": metadata.get("uid", ""),
        "created_at": k8s_time(metadata.get("creationTimestamp")),
        "generation": metadata.get("generation"), "observed_generation": status.get("observedGeneration"),
        "replicas": spec.get("replicas", 1) if spec.get("replicas") is not None else 1,
        "ready_replicas": status.get("readyReplicas") or 0, "updated_replicas": status.get("updatedReplicas") or 0,
        "available_replicas": status.get("availableReplicas") or 0,
        "unavailable_replicas": status.get("unavailableReplicas") or 0,
        "revision": (metadata.get("annotations") or {}).get(REVISION_ANNOTATION, ""),
        "strategy": {"type": strategy.get("type", "RollingUpdate"), "max_surge": rolling.get("maxSurge"),
                     "max_unavailable": rolling.get("maxUnavailable")},
        "state": state, "message": message, "paused": bool(spec.get("paused")),
        "conditions": [{"type": c.get("type", ""), "status": c.get("status", ""), "reason": _text(c.get("reason"), 200),
                        "message": _text(c.get("message")), "last_update": k8s_time(c.get("lastUpdateTime")),
                        "last_transition": k8s_time(c.get("lastTransitionTime"))} for c in status.get("conditions") or []],
        "labels": (template.get("metadata") or {}).get("labels") or {},
        "selector": spec.get("selector") or {},
        "annotations": {k: v for k, v in annotations.items() if k in PLATFORM_ANNOTATIONS},
        "containers": [{"name": c.get("name", ""), "image": c.get("image", ""),
                        "resources": {"requests": (c.get("resources") or {}).get("requests") or {},
                                      "limits": (c.get("resources") or {}).get("limits") or {}},
                        "probes": {kind: _probe_text(c.get(kind + "Probe")) for kind in ("startup", "readiness", "liveness")
                                   if c.get(kind + "Probe")}}
                       for c in (template.get("spec") or {}).get("containers") or []],
    }


def summarize_replicaset(item: Dict[str, Any], current_revision: str) -> Dict[str, Any]:
    metadata, spec, status = _meta(item), item.get("spec") or {}, item.get("status") or {}
    revision = (metadata.get("annotations") or {}).get(REVISION_ANNOTATION, "")
    containers = ((spec.get("template") or {}).get("spec") or {}).get("containers") or []
    return {
        "name": metadata.get("name", ""), "revision": revision, "current": bool(revision) and revision == current_revision,
        "replicas": spec.get("replicas") or 0, "ready_replicas": status.get("readyReplicas") or 0,
        "available_replicas": status.get("availableReplicas") or 0,
        "created_at": k8s_time(metadata.get("creationTimestamp")),
        "pod_template_hash": (metadata.get("labels") or {}).get("pod-template-hash", ""),
        "images": [c.get("image", "") for c in containers],
        "deployment_id": (((spec.get("template") or {}).get("metadata") or {}).get("annotations") or {}).get(
            "swr-builder/deployment-id", ""),
    }


# ------------------------------------------------------------------ Service / Ingress / HPA / Event
def summarize_service(item: Dict[str, Any]) -> Dict[str, Any]:
    spec = item.get("spec") or {}
    return {"name": _meta(item).get("name", ""), "type": spec.get("type", "ClusterIP"), "cluster_ip": spec.get("clusterIP", ""),
            "selector": spec.get("selector") or {},
            "ports": [{"name": p.get("name", ""), "port": p.get("port"), "targetPort": p.get("targetPort"),
                       "nodePort": p.get("nodePort"), "protocol": p.get("protocol", "TCP")} for p in spec.get("ports") or []]}


def summarize_ingress(item: Dict[str, Any]) -> Dict[str, Any]:
    spec, annotations = item.get("spec") or {}, _meta(item).get("annotations") or {}
    rules = []
    for rule in spec.get("rules") or []:
        paths = [{"path": p.get("path", "/"), "path_type": p.get("pathType", ""),
                  "service": ((p.get("backend") or {}).get("service") or {}).get("name", ""),
                  "port": (((p.get("backend") or {}).get("service") or {}).get("port") or {}).get("number")
                  or (((p.get("backend") or {}).get("service") or {}).get("port") or {}).get("name")}
                 for p in (rule.get("http") or {}).get("paths") or []]
        rules.append({"host": rule.get("host", ""), "paths": paths})
    https = bool(spec.get("tls")) or bool(annotations.get("kubernetes.io/elb.tls-certificate-ids"))
    return {"name": _meta(item).get("name", ""), "ingress_class": spec.get("ingressClassName", ""),
            "hosts": [r["host"] for r in rules], "rules": rules,
            "service_names": sorted({p["service"] for r in rules for p in r["paths"] if p["service"]}),
            "https": https, "port": annotations.get("kubernetes.io/elb.port", ""),
            "inconsistent": annotations.get(INCONSISTENT_ANNOTATION, ""),
            "listener_master": annotations.get("kubernetes.io/elb.listener-master-ingress", "")}


def summarize_hpa(item: Dict[str, Any]) -> Dict[str, Any]:
    spec, status = item.get("spec") or {}, item.get("status") or {}
    targets = []
    for metric in spec.get("metrics") or []:
        resource = metric.get("resource") or {}
        target = resource.get("target") or {}
        if metric.get("type") == "Resource":
            targets.append({"resource": resource.get("name", ""), "utilization": target.get("averageUtilization"),
                            "value": target.get("averageValue")})
    current = {}
    for metric in status.get("currentMetrics") or []:
        resource = metric.get("resource") or {}
        if metric.get("type") == "Resource":
            current[resource.get("name", "")] = (resource.get("current") or {}).get("averageUtilization")
    return {"name": _meta(item).get("name", ""), "min_replicas": spec.get("minReplicas", 1),
            "max_replicas": spec.get("maxReplicas"), "current_replicas": status.get("currentReplicas"),
            "desired_replicas": status.get("desiredReplicas"), "targets": targets, "current": current,
            "last_scale_time": k8s_time(status.get("lastScaleTime")),
            "conditions": [{"type": c.get("type", ""), "status": c.get("status", ""), "reason": _text(c.get("reason"), 200),
                            "message": _text(c.get("message"), 500)} for c in status.get("conditions") or []]}


def summarize_event(item: Dict[str, Any]) -> Dict[str, Any]:
    involved = item.get("involvedObject") or item.get("regarding") or {}
    created = _meta(item).get("creationTimestamp")
    series = item.get("series") or {}
    last = item.get("lastTimestamp") or series.get("lastObservedTime") or item.get("eventTime") or created
    first = item.get("firstTimestamp") or item.get("eventTime") or created
    return {"type": item.get("type") or "Normal", "reason": _text(item.get("reason"), 200),
            "message": _text(item.get("message") or item.get("note")), "kind": involved.get("kind", ""),
            "name": involved.get("name", ""), "count": item.get("count") or series.get("count") or 1,
            "first_seen": k8s_time(first), "last_seen": k8s_time(last),
            "source": (item.get("source") or {}).get("component") or item.get("reportingComponent") or ""}


def access_points(namespace: str, services: List[Dict[str, Any]], ingresses: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    points: List[Dict[str, str]] = []
    for ingress in ingresses:
        scheme = "https" if ingress["https"] else "http"
        port = ingress["port"] if ingress["port"] not in ("", "80", "443") else ""
        for rule in ingress["rules"]:
            for path in rule["paths"] or [{"path": "/"}]:
                if rule["host"]:
                    points.append({"type": "ingress", "label": f"Ingress {ingress['name']}",
                                   "address": f"{scheme}://{rule['host']}{':' + port if port else ''}{path['path'] or '/'}"})
    for service in services:
        for port in service["ports"]:
            points.append({"type": "cluster", "label": f"集群内 · {port['name'] or port['port']}",
                           "address": f"{service['name']}.{namespace}.svc:{port['port']}"})
            if service["type"] in ("NodePort", "LoadBalancer") and port.get("nodePort"):
                points.append({"type": "node", "label": f"节点端口 · {port['name'] or port['port']}",
                               "address": f"<节点 IP>:{port['nodePort']}"})
    return points


# ------------------------------------------------------------------ 查询
def _paths(namespace: str) -> Dict[str, str]:
    name = quote(namespace, safe="")
    return {"deployments": f"/apis/apps/v1/namespaces/{name}/deployments",
            "replicasets": f"/apis/apps/v1/namespaces/{name}/replicasets",
            "services": f"/api/v1/namespaces/{name}/services", "pods": f"/api/v1/namespaces/{name}/pods",
            "ingresses": f"/apis/networking.k8s.io/v1/namespaces/{name}/ingresses",
            "hpas": f"/apis/autoscaling/v2/namespaces/{name}/horizontalpodautoscalers",
            "events": f"/api/v1/namespaces/{name}/events"}


async def _optional(warnings: List[str], call: Callable[[], Awaitable[List[Dict[str, Any]]]]) -> List[Dict[str, Any]]:
    """可选资源读取失败（例如凭据没有 HPA 或事件的 list 权限）时记为警告，不影响其他信息。"""
    try:
        return await call()
    except DomainError as exc:
        warnings.append(str(exc))
        return []


async def discover_cluster(ctx, cluster_id):
    cluster, credential = load_cluster(ctx.store, ctx.box, cluster_id)
    warnings: List[str] = []
    async with KubeClient(cluster["server"], credential, ctx.deploy_transport) as kube:
        version = await kube.version()
        rows = await kube.list_resources("/api/v1/namespaces")
        nodes = await _optional(warnings, lambda: kube.list_resources("/api/v1/nodes"))
    managed = {n["name"]: n for n in ctx.store.list_namespaces(cluster_id=cluster_id)}
    namespaces = [{"name": _meta(n).get("name", ""), "status": (n.get("status") or {}).get("phase", "Unknown"),
                   "managed_id": managed.get(_meta(n).get("name"), {}).get("id")} for n in rows]
    nodes = [{"name": _meta(n).get("name", ""),
              "version": ((n.get("status") or {}).get("nodeInfo") or {}).get("kubeletVersion", ""),
              "ready": any(c.get("type") == "Ready" and c.get("status") == "True"
                           for c in (n.get("status") or {}).get("conditions") or []),
              "architecture": ((n.get("status") or {}).get("nodeInfo") or {}).get("architecture", "")}
             for n in nodes]
    ctx.store.set_cluster_status(cluster_id, "ready")
    return {"cluster_id": cluster_id, "version": version, "namespaces": namespaces, "nodes": nodes,
            "warnings": warnings, "fetched_at": utcnow()}


async def namespace_resources(ctx, namespace):
    """命名空间内的工作负载、Service、Pod 与 Ingress（新建服务时选择 Deployment 使用）。"""
    cluster, credential = load_cluster(ctx.store, ctx.box, namespace["cluster_id"])
    paths = _paths(namespace["name"])
    warnings: List[str] = []
    async with KubeClient(cluster["server"], credential, ctx.deploy_transport) as kube:
        deployments = await kube.list_resources(paths["deployments"])
        services = await _optional(warnings, lambda: kube.list_resources(paths["services"]))
        pods = await _optional(warnings, lambda: kube.list_resources(paths["pods"]))
        ingresses = await _optional(warnings, lambda: kube.list_resources(paths["ingresses"]))
    return {"namespace_id": namespace["id"], "cluster_name": namespace["cluster_name"],
            "namespace_name": namespace["name"], "deployments": [summarize_deployment(d) for d in deployments],
            "services": [summarize_service(s) for s in services], "pods": [summarize_pod(p) for p in pods],
            "ingresses": [summarize_ingress(i) for i in ingresses], "warnings": warnings, "fetched_at": utcnow()}


def service_target(store, service: Dict[str, Any]) -> Tuple[str, str]:
    """服务对应的 Deployment 与容器名称：纳管服务取 spec.target，托管服务按环境档案的命名规则计算。"""
    spec = service.get("spec") or {}
    if spec.get("mode") == "managed":
        from ..delivery.spec import object_names
        from .environments import merged_profile
        # 已发布过的服务以最近一次成功下发的对象为准（命名规则或 base 修改后尚未发布时，线上仍是旧对象）
        manifest = store.last_success_manifest(service["id"]) if service.get("id") else None
        deployed = next((o for o in (manifest or {}).get("objects", []) if o["kind"] == "Deployment"), None)
        if deployed:
            return deployed["metadata"]["name"], deployed["spec"]["template"]["spec"]["containers"][0]["name"]
        row = store.get_environment_profile(service["namespace_id"])
        namespace = store.get_namespace(service["namespace_id"]) or {}
        try:
            names = object_names(spec, service["name"], namespace.get("name", ""),
                                 merged_profile(row["profile"] if row else None))
        except ValueError:
            return "", ""
        return names["deployment"], names["container"]
    target = spec.get("target") or {}
    return target.get("name", ""), target.get("container", "")


def _related_events(events: Iterable[Dict[str, Any]], objects: Iterable[Tuple[str, str]]) -> List[Dict[str, Any]]:
    wanted = set(objects)
    rows = [summarize_event(e) for e in events]
    rows = [e for e in rows if (e["kind"], e["name"]) in wanted]
    rows.sort(key=lambda e: e["last_seen"] or "", reverse=True)
    return rows[:EVENT_LIMIT]


async def service_runtime(ctx, service: Dict[str, Any]) -> Dict[str, Any]:
    """服务的运行状态：Deployment 与滚动进度、Pod（状态、重启、启动时间）、ReplicaSet 历史、
    Service / Ingress / HPA、最近事件与访问地址。只读取与该服务相关的对象。"""
    namespace = ctx.store.get_namespace(service["namespace_id"])
    cluster, credential = load_cluster(ctx.store, ctx.box, namespace["cluster_id"])
    name, container_name = service_target(ctx.store, service)
    paths = _paths(namespace["name"])
    warnings: List[str] = []
    pods: List[Dict[str, Any]] = []
    replicasets: List[Dict[str, Any]] = []
    async with KubeClient(cluster["server"], credential, ctx.deploy_transport) as kube:
        raw = await kube.get_json(paths["deployments"] + "/" + quote(name, safe="")) if name else None
        if raw:
            spec = raw.get("spec") or {}
            selector = spec.get("selector") or {
                "matchLabels": ((spec.get("template") or {}).get("metadata") or {}).get("labels") or {}}
            query = {"labelSelector": selector_string(selector)}
            pods = [p for p in await _optional(warnings, lambda: kube.list_resources(paths["pods"], query))
                    if selector_matches(selector, _meta(p).get("labels") or {})]
            replicasets = [r for r in await _optional(warnings, lambda: kube.list_resources(paths["replicasets"], query))
                           if any(o.get("kind") == "Deployment" and o.get("name") == name
                                  for o in _meta(r).get("ownerReferences") or [])]
        services = await _optional(warnings, lambda: kube.list_resources(paths["services"]))
        ingresses = await _optional(warnings, lambda: kube.list_resources(paths["ingresses"]))
        hpas = await _optional(warnings, lambda: kube.list_resources(paths["hpas"])) if raw else []
        events = await _optional(warnings, lambda: kube.list_resources(paths["events"], max_items=1000)) if raw else []

    deployment = summarize_deployment(raw) if raw else None
    labels = deployment["labels"] if deployment else {}
    matched_services = [summarize_service(s) for s in services
                        if raw and (s.get("spec") or {}).get("selector")
                        and all(labels.get(k) == v for k, v in ((s.get("spec") or {}).get("selector") or {}).items())]
    service_names = {s["name"] for s in matched_services}
    matched_ingresses = [i for i in (summarize_ingress(i) for i in ingresses) if service_names & set(i["service_names"])]
    hpa = next((summarize_hpa(h) for h in hpas if ((h.get("spec") or {}).get("scaleTargetRef") or {}).get("name") == name
                and ((h.get("spec") or {}).get("scaleTargetRef") or {}).get("kind") == "Deployment"), None)
    current_revision = deployment["revision"] if deployment else ""
    rs_rows = sorted((summarize_replicaset(r, current_revision) for r in replicasets),
                     key=lambda r: int(r["revision"]) if r["revision"].isdigit() else 0, reverse=True)
    revisions = {r["pod_template_hash"]: r["revision"] for r in rs_rows if r["pod_template_hash"]}
    pod_rows = sorted((summarize_pod(p) for p in pods), key=lambda p: p["created_at"] or "", reverse=True)
    for pod in pod_rows:
        pod["revision"] = revisions.get(pod["pod_template_hash"], "")
        pod["current"] = bool(pod["revision"]) and pod["revision"] == current_revision
    objects = [("Deployment", name)] + [("ReplicaSet", r["name"]) for r in rs_rows] + \
              [("Pod", p["name"]) for p in pod_rows] + [("Service", s) for s in service_names] + \
              [("Ingress", i["name"]) for i in matched_ingresses] + ([("HorizontalPodAutoscaler", hpa["name"])] if hpa else [])
    event_rows = _related_events(events, objects)
    container = next((c for c in (deployment or {}).get("containers", []) if c["name"] == container_name), None)
    if deployment and not container:
        warnings.append(f"Deployment {name} 中没有名为 {container_name} 的容器")
    if any(i["inconsistent"] for i in matched_ingresses):
        warnings.append("CCE 标记 Ingress 的监听器配置与共享监听器不一致（kubernetes.io/elb.inconsistent-listener-config），"
                        "该 Ingress 写的证书或监听设置没有生效")
    running_images = sorted({c["image"] + ("@" + c["digest"] if c["digest"] else "")
                             for p in pod_rows for c in p["containers"] if c["name"] == container_name})
    summary = {
        "state": deployment["state"] if deployment else "missing",
        "message": deployment["message"] if deployment else f"集群中没有名为 {name} 的 Deployment",
        "desired": deployment["replicas"] if deployment else 0,
        "ready": deployment["ready_replicas"] if deployment else 0,
        "available": deployment["available_replicas"] if deployment else 0,
        "updated": deployment["updated_replicas"] if deployment else 0,
        "pods": len(pod_rows), "restarts": sum(p["restarts"] for p in pod_rows),
        "warning_events": sum(1 for e in event_rows if e["type"] == "Warning"),
        "image": container["image"] if container else "", "running_images": running_images,
        "created_at": deployment["created_at"] if deployment else None,
        "deployment_id": (deployment or {}).get("annotations", {}).get("swr-builder/deployment-id", ""),
    }
    return {"namespace_id": namespace["id"], "cluster_name": namespace["cluster_name"],
            "namespace_name": namespace["name"], "fetched_at": utcnow(), "warnings": warnings,
            "found": bool(deployment), "target": {"kind": "Deployment", "name": name, "container": container_name},
            "summary": summary, "deployment": deployment, "deployments": [deployment] if deployment else [],
            "container": container, "pods": pod_rows, "replicasets": rs_rows, "services": matched_services,
            "ingresses": matched_ingresses, "hpa": hpa, "events": event_rows,
            "access": access_points(namespace["name"], matched_services, matched_ingresses)}


async def services_status(ctx, services: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """服务列表的运行状态：每个命名空间只读取一次 Deployment 列表。读取失败的命名空间状态为 unknown。"""
    groups: Dict[int, List[Dict[str, Any]]] = {}
    for service in services:
        groups.setdefault(service["namespace_id"], []).append(service)
    semaphore = asyncio.Semaphore(STATUS_CONCURRENCY)

    async def one(namespace_id: int, members: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        namespace = ctx.store.get_namespace(namespace_id)
        targets = {s["id"]: service_target(ctx.store, s) for s in members}
        try:
            async with semaphore:
                cluster, credential = load_cluster(ctx.store, ctx.box, namespace["cluster_id"])
                async with KubeClient(cluster["server"], credential, ctx.deploy_transport) as kube:
                    rows = await kube.list_resources(_paths(namespace["name"])["deployments"])
        except DomainError as exc:
            return [{"service_id": s["id"], "state": "unknown", "message": str(exc)[:300]} for s in members]
        by_name = {_meta(d).get("name"): d for d in rows}
        result = []
        for service in members:
            name, container_name = targets[service["id"]]
            item = by_name.get(name)
            if not item:
                result.append({"service_id": service["id"], "state": "missing", "message": f"集群中没有 Deployment {name}",
                               "ready": 0, "desired": 0, "image": ""})
                continue
            summary = summarize_deployment(item)
            image = next((c["image"] for c in summary["containers"] if c["name"] == container_name), "")
            result.append({"service_id": service["id"], "state": summary["state"], "message": summary["message"],
                           "ready": summary["ready_replicas"], "desired": summary["replicas"], "image": image,
                           "deployment_id": summary["annotations"].get("swr-builder/deployment-id", "")})
        return result

    batches = await asyncio.gather(*(one(nid, members) for nid, members in groups.items()))
    return [row for batch in batches for row in batch]
