"""服务目录：服务 spec 校验、响应格式、修订对比。"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from .namespaces import effective_policy

SERVICE_RESPONSE_FIELDS = ("id", "name", "project_id", "namespace_id", "app_id", "current_revision_id",
                           "deployed_revision_id", "spec")
SPEC_KEYS = {"mode", "target", "workload", "container", "config_files", "service", "ingress", "hpa", "scheduling",
             "image_pull_secrets", "labels", "annotations"}
_DNS_SUBDOMAIN_RE = re.compile(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?)*")
_DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?")


def service_response(service: Dict[str, Any]) -> Dict[str, Any]:
    return {key: service.get(key) for key in SERVICE_RESPONSE_FIELDS}


def normalize_service_spec(value: Any, namespace: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) - SPEC_KEYS:
        raise ValueError("服务 spec 包含未知字段")
    mode = value.get("mode")
    if mode == "image_only":
        target = value.get("target")
        if set(value) != {"mode", "target"} or not isinstance(target, dict) or set(target) != {"kind", "name", "container"}:
            raise ValueError("image_only spec 只允许 kind/name/container")
        if (target["kind"] != "Deployment" or not isinstance(target["name"], str)
                or not _DNS_SUBDOMAIN_RE.fullmatch(target["name"]) or len(target["name"]) > 253):
            raise ValueError("image_only Deployment 名称无效")
        if not isinstance(target["container"], str) or not _DNS_LABEL_RE.fullmatch(target["container"]):
            raise ValueError("容器名称无效")
        return value
    if mode != "managed":
        raise ValueError("mode 必须是 managed 或 image_only")
    policy = effective_policy(namespace["policy"])
    if "workload" in value:
        workload = value["workload"]
        if not isinstance(workload, dict) or set(workload) - {"kind", "replicas", "strategy", "progress_deadline_seconds",
                                                              "revision_history_limit", "termination_grace_period_seconds"}:
            raise ValueError("workload 配置包含未知字段")
        if workload.get("kind", "Deployment") != "Deployment":
            raise ValueError("当前只支持 Deployment workload")
        replicas = workload.get("replicas", 1)
        if type(replicas) is not int or not 0 <= replicas <= policy["max_replicas"]:
            raise ValueError("副本数超出命名空间策略")
    if "service" in value:
        service = value["service"]
        if not isinstance(service, dict) or set(service) - {"enabled", "type", "ports"}:
            raise ValueError("Service 配置包含未知字段")
        if service.get("type", "ClusterIP") not in policy["service_types"]:
            raise ValueError("Service 类型不符合命名空间策略")
    if "image_pull_secrets" in value:
        names = value["image_pull_secrets"]
        if not isinstance(names, list) or any(name not in policy["image_pull_secrets"] for name in names):
            raise ValueError("imagePullSecrets 不符合命名空间策略")
    if "hpa" in value and value["hpa"].get("enabled") and not policy["hpa_enabled"]:
        raise ValueError("命名空间策略未启用 HPA")
    if "ingress" in value and value["ingress"].get("enabled") and not policy["ingress"].get("enabled"):
        raise ValueError("命名空间策略未启用 Ingress")
    return value


def revision_diff(old: Any, new: Any) -> List[Dict[str, Any]]:
    """逐键比较两个 spec，返回 [{path, old, new}]；path 形如 /target/name。"""
    changes: List[Dict[str, Any]] = []

    def compare(path: str, left: Any, right: Any) -> None:
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right)):
                compare(path + "/" + str(key), left.get(key), right.get(key))
        elif left != right:
            changes.append({"path": path or "/", "old": left, "new": right})

    compare("", old, new)
    return changes
