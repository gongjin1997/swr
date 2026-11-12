"""内存中的 Kubernetes API，用于托管服务、导入与运行状态的测试。

支持平台用到的部分：GET / LIST（labelSelector 等值匹配、分页参数忽略）/ Server-Side Apply（含 dryRun、按字段管理者
记录 spec 顶层字段的归属）/ strategic merge patch（只处理容器镜像与 Pod 模板注解）/ DELETE / SSAR。
Deployment 写入后按 ready 标志立即完成滚动：生成 ReplicaSet 和 Pod（containerStatuses 带 imageID digest）。
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

GROUPS = {"configmaps": "ConfigMap", "secrets": "Secret", "services": "Service", "pods": "Pod", "events": "Event",
          "deployments": "Deployment", "replicasets": "ReplicaSet", "ingresses": "Ingress",
          "horizontalpodautoscalers": "HorizontalPodAutoscaler"}
API_VERSIONS = {"ConfigMap": "v1", "Secret": "v1", "Service": "v1", "Pod": "v1", "Event": "v1", "Deployment": "apps/v1",
                "ReplicaSet": "apps/v1", "Ingress": "networking.k8s.io/v1", "HorizontalPodAutoscaler": "autoscaling/v2"}
NOW = "2026-11-11T08:00:00Z"


def _merge(base: Any, patch: Any) -> Any:
    if isinstance(base, dict) and isinstance(patch, dict):
        result = dict(base)
        for key, value in patch.items():
            result[key] = _merge(base.get(key), value)
        return result
    return copy.deepcopy(patch)


class FakeKube:
    def __init__(self, namespaces=("sit",)):
        self.namespaces = set(namespaces)
        self.objects: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        self.requests: List[Tuple[str, str, Dict[str, str]]] = []
        self.fail: Dict[Tuple[str, str, str], Tuple[int, str]] = {}
        # 每个请求处理前调用（测试用来在发布进行中改变平台状态）
        self.hook: Optional[Callable[[httpx.Request], None]] = None
        self.ready = True
        self.version = 1
        self.owners: Dict[Tuple[str, str, str], Dict[str, set]] = {}

    # ------------------------------------------------------------------ 辅助
    def put(self, obj: Dict[str, Any]) -> Dict[str, Any]:
        """直接写入对象（模拟集群中已有的资源）。"""
        kind = obj["kind"]
        plural = next(p for p, k in GROUPS.items() if k == kind)
        meta = obj.setdefault("metadata", {})
        namespace = meta.setdefault("namespace", sorted(self.namespaces)[0])
        meta.setdefault("uid", f"uid-{kind.lower()}-{meta['name']}")
        meta.setdefault("creationTimestamp", NOW)
        meta["resourceVersion"] = str(self._bump())
        obj.setdefault("apiVersion", API_VERSIONS[kind])
        self.objects[(plural, namespace, meta["name"])] = obj
        if kind == "Deployment":
            meta.setdefault("generation", 1)
            self._rollout(namespace, obj)
        return obj

    def get(self, plural: str, name: str, namespace: Optional[str] = None) -> Optional[Dict[str, Any]]:
        return self.objects.get((plural, namespace or sorted(self.namespaces)[0], name))

    def _bump(self) -> int:
        self.version += 1
        return self.version

    def _rollout(self, namespace: str, deployment: Dict[str, Any]) -> None:
        """控制器：按当前模板生成 ReplicaSet 与 Pod，并更新 Deployment 状态。"""
        meta, spec = deployment["metadata"], deployment.setdefault("spec", {})
        template = spec.get("template") or {}
        digest = hashlib.sha256(json.dumps(template, sort_keys=True).encode()).hexdigest()
        pod_hash = digest[:10]
        replicas = spec.get("replicas", 1)
        revision = str(sum(1 for (p, ns, _), o in self.objects.items() if p == "replicasets" and ns == namespace and
                           any(r.get("name") == meta["name"] for r in o["metadata"].get("ownerReferences", []))) + 1)
        labels = dict((template.get("metadata") or {}).get("labels") or {})
        rs_name = f"{meta['name']}-{pod_hash}"
        existing = self.objects.get(("replicasets", namespace, rs_name))
        if existing:
            revision = existing["metadata"]["annotations"]["deployment.kubernetes.io/revision"]
        rs = {"apiVersion": "apps/v1", "kind": "ReplicaSet",
              "metadata": {"name": rs_name, "namespace": namespace, "labels": {**labels, "pod-template-hash": pod_hash},
                           "annotations": {"deployment.kubernetes.io/revision": revision}, "creationTimestamp": NOW,
                           "ownerReferences": [{"kind": "Deployment", "name": meta["name"]}]},
              "spec": {"replicas": replicas if self.ready else 0, "template": copy.deepcopy(template)},
              "status": {"readyReplicas": replicas if self.ready else 0, "availableReplicas": replicas if self.ready else 0}}
        self.objects[("replicasets", namespace, rs_name)] = rs
        meta.setdefault("annotations", {})["deployment.kubernetes.io/revision"] = revision
        for key in [k for k in self.objects if k[0] == "pods" and k[1] == namespace
                    and self.objects[k]["metadata"].get("labels", {}).get("swr-test/owner") == meta["name"]]:
            del self.objects[key]
        containers = (template.get("spec") or {}).get("containers") or []
        for index in range(replicas if self.ready else 0):
            name = f"{rs_name}-p{index}"
            self.objects[("pods", namespace, name)] = {
                "apiVersion": "v1", "kind": "Pod",
                "metadata": {"name": name, "namespace": namespace, "creationTimestamp": NOW,
                             "labels": {**labels, "pod-template-hash": pod_hash, "swr-test/owner": meta["name"]}},
                "spec": {"nodeName": f"node-{index}", "containers": copy.deepcopy(containers)},
                "status": {"phase": "Running", "startTime": NOW, "podIP": f"10.0.0.{index + 10}", "hostIP": "192.0.2.1",
                           "qosClass": "Burstable", "conditions": [{"type": "Ready", "status": "True"}],
                           "containerStatuses": [{"name": c["name"], "image": c.get("image", ""), "ready": True,
                                                  "started": True, "restartCount": 0,
                                                  "imageID": c.get("image", "").split("@")[0] + "@sha256:" + ("e" * 64
                                                             if "@sha256:" not in c.get("image", "")
                                                             else c["image"].rsplit("@sha256:", 1)[1]),
                                                  "state": {"running": {"startedAt": NOW}}} for c in containers]}}
        generation = meta.get("generation", 1)
        deployment["status"] = {"observedGeneration": generation, "replicas": replicas,
                                "updatedReplicas": replicas if self.ready else 0,
                                "readyReplicas": replicas if self.ready else 0,
                                "availableReplicas": replicas if self.ready else 0,
                                "conditions": [{"type": "Available", "status": "True" if self.ready else "False"},
                                               {"type": "Progressing", "status": "True",
                                                "reason": "NewReplicaSetAvailable" if self.ready else "ReplicaSetUpdated"}]}

    @staticmethod
    def _matches(obj: Dict[str, Any], selector: str) -> bool:
        if not selector:
            return True
        labels = obj["metadata"].get("labels") or {}
        for part in selector.split(","):
            if "=" in part and " " not in part:
                key, value = part.split("=", 1)
                if labels.get(key) != value:
                    return False
        return True

    def _owned(self, key: Tuple[str, str, str]) -> Dict[str, set]:
        return self.owners.setdefault(key, {})

    def _managed_fields(self, key: Tuple[str, str, str]) -> List[Dict[str, Any]]:
        return [{"manager": manager, "operation": "Apply",
                 "fieldsV1": {"f:spec": {f"f:{field}": {} for field in sorted(fields)}}}
                for manager, fields in self._owned(key).items()]

    # ------------------------------------------------------------------ HTTP
    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        params = dict(request.url.params)
        if self.hook:
            self.hook(request)
        self.requests.append((method, path, params))
        if path == "/version":
            return httpx.Response(200, json={"gitVersion": "v1.31.1"})
        if path == "/apis/authorization.k8s.io/v1/selfsubjectaccessreviews":
            return httpx.Response(201, json={"status": {"allowed": True}})
        if path == "/api/v1/namespaces":
            return httpx.Response(200, json={"items": [{"metadata": {"name": n}, "status": {"phase": "Active"}}
                                                       for n in sorted(self.namespaces)]})
        parts = path.strip("/").split("/")
        if parts[:3] == ["api", "v1", "namespaces"] and len(parts) == 4:
            return httpx.Response(200 if parts[3] in self.namespaces else 404, json={"metadata": {"name": parts[3]}})
        if parts[0] == "api":
            rest = parts[2:]
        elif parts[0] == "apis":
            rest = parts[3:]
        else:
            return httpx.Response(404)
        if len(rest) < 3 or rest[0] != "namespaces" or rest[2] not in GROUPS:
            return httpx.Response(404, json={"message": "not found"})
        namespace, plural = rest[1], rest[2]
        name = rest[3] if len(rest) > 3 else None
        if namespace not in self.namespaces:
            return httpx.Response(404, json={"message": f"namespaces {namespace} not found"})
        failure = self.fail.get((method, plural, name or ""))
        if failure and not (method == "PATCH" and params.get("dryRun") and failure[0] != 422):
            return httpx.Response(failure[0], json={"kind": "Status", "message": failure[1]})
        key = (plural, namespace, name or "")
        if name is None:
            if method != "GET":
                return httpx.Response(405)
            items = [copy.deepcopy(o) for (p, ns, _), o in sorted(self.objects.items()) if p == plural and ns == namespace
                     and self._matches(o, params.get("labelSelector", ""))]
            return httpx.Response(200, json={"items": items, "metadata": {}})
        current = self.objects.get(key)
        if method == "GET":
            if not current:
                return httpx.Response(404, json={"message": f"{plural} {name} not found"})
            item = copy.deepcopy(current)
            item["metadata"]["managedFields"] = self._managed_fields(key)
            return httpx.Response(200, json=item)
        if method == "DELETE":
            if not current:
                return httpx.Response(404, json={"message": "not found"})
            if not params.get("dryRun"):
                del self.objects[key]
                self.owners.pop(key, None)
            return httpx.Response(200, json={"kind": "Status", "status": "Success"})
        if method == "PATCH":
            content_type = request.headers.get("content-type", "")
            body = json.loads(request.content)
            if content_type.startswith("application/apply-patch"):
                return self._apply(key, current, body, params)
            if not current:
                return httpx.Response(404, json={"message": "not found"})
            template = body.get("spec", {}).get("template", {})
            updated = copy.deepcopy(current)
            target = updated["spec"]["template"]
            target.setdefault("metadata", {}).setdefault("annotations", {}).update(
                (template.get("metadata") or {}).get("annotations") or {})
            for patch in (template.get("spec") or {}).get("containers") or []:
                for container in target["spec"]["containers"]:
                    if container["name"] == patch["name"]:
                        container.update(patch)
            return self._store(key, updated, changed=True)
        return httpx.Response(405)

    def _apply(self, key, current, body, params) -> httpx.Response:
        manager = params.get("fieldManager", "")
        kind = body.get("kind")
        plural = key[0]
        if GROUPS.get(plural) != kind or body.get("metadata", {}).get("name") != key[2]:
            return httpx.Response(400, json={"message": "kind or name mismatch"})
        if kind == "Secret":
            for value in (body.get("data") or {}).values():
                if not isinstance(value, str):
                    return httpx.Response(422, json={"message": "Secret data must be base64 strings"})
        merged = _merge(current or {}, body)
        merged.setdefault("metadata", {})["namespace"] = key[1]
        owned = self._owned(key) if not params.get("dryRun") else copy.deepcopy(self._owned(key))
        fields = set((body.get("spec") or {}).keys())
        previous = owned.get(manager, set())
        if "replicas" in previous and "replicas" not in fields and kind == "Deployment":
            others = [m for m, f in owned.items() if m != manager and "replicas" in f]
            if not others:
                merged["spec"].pop("replicas", None)
                merged["spec"]["replicas"] = 1
        owned[manager] = fields
        changed = current is None or json.dumps(current.get("spec"), sort_keys=True) != json.dumps(merged.get("spec"), sort_keys=True) \
            or json.dumps(current.get("data"), sort_keys=True) != json.dumps(merged.get("data"), sort_keys=True) \
            or (current.get("metadata") or {}).get("annotations") != merged["metadata"].get("annotations") \
            or (current.get("metadata") or {}).get("labels") != merged["metadata"].get("labels")
        if params.get("dryRun"):
            return httpx.Response(200 if current else 201, json=merged)
        return self._store(key, merged, changed=changed, created=current is None)

    def _store(self, key, obj, changed: bool, created: bool = False) -> httpx.Response:
        meta = obj.setdefault("metadata", {})
        meta.setdefault("uid", f"uid-{key[0]}-{key[2]}")
        meta.setdefault("creationTimestamp", NOW)
        obj.setdefault("apiVersion", API_VERSIONS[GROUPS[key[0]]])
        if changed:
            meta["resourceVersion"] = str(self._bump())
            if obj.get("kind") == "Deployment":
                meta["generation"] = meta.get("generation", 0) + 1
        self.objects[key] = obj
        if obj.get("kind") == "Deployment" and changed:
            self._rollout(key[1], obj)
        return httpx.Response(201 if created else 200, json=copy.deepcopy(obj))
