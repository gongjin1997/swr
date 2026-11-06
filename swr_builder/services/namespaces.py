"""命名空间发布策略与 RBAC 自检。"""
from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..apps import ORG_RE
from ..validation import DNS_LABEL_RE

TIERS = ("dev", "test", "staging", "prod")

DEFAULT_POLICY: Dict[str, Any] = {
    "require_approval": True, "min_approvals": 1, "allow_auto_deploy": False, "allowed_registry_orgs": [],
    "service_types": ["ClusterIP"], "ingress": {"enabled": False, "host_suffixes": [], "ingress_class": ""},
    "hpa_enabled": False, "resources": {"require_limits": True}, "max_replicas": 10,
    "require_readiness_probe": True, "image_pull_secrets": [], "managed_pull_secret": False,
    "deploy_windows": [], "freeze": {"enabled": False, "until": None, "reason": ""}, "auto_rollback": False,
    "rollout_timeout": 600,
}
POLICY_KEYS = set(DEFAULT_POLICY)
_BOOL_KEYS = ("require_approval", "allow_auto_deploy", "hpa_enabled", "require_readiness_probe",
              "managed_pull_secret", "auto_rollback")
_NESTED_DEFAULTS = ("ingress", "resources", "freeze")
_QUANTITY_RE = re.compile(r"^(?:[0-9]+(?:\.[0-9]+)?|[0-9]+m)(?:Ki|Mi|Gi|Ti|Pi|Ei)?$")
_HHMM_RE = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
_DNS_SUFFIX_RE = re.compile(r"\.[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?")


def effective_policy(policy: Any) -> Dict[str, Any]:
    """用默认值补齐策略中缺失的键（不做校验），供发布流程读取。早期写入的策略可能缺少后来新增的键。"""
    result = deepcopy(DEFAULT_POLICY)
    if isinstance(policy, dict):
        for key, value in policy.items():
            if key in _NESTED_DEFAULTS and isinstance(value, dict):
                result[key] = {**result[key], **value}
            else:
                result[key] = value
    return result


def required_approvals(policy: Dict[str, Any], tier: str) -> int:
    """发布需要的审批人数。生产环境（prod）始终至少需要 1 人审批。"""
    if policy.get("require_approval", True) or tier == "prod":
        return max(1, int(policy.get("min_approvals", 1)))
    return 0


def normalize_namespace_policy(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict) or set(data) - POLICY_KEYS:
        raise ValueError("命名空间策略包含未知字段")
    policy = deepcopy(DEFAULT_POLICY)
    policy.update(data)
    for key in _BOOL_KEYS:
        if not isinstance(policy[key], bool):
            raise ValueError(f"{key} 必须是布尔值")
    if type(policy["min_approvals"]) is not int or not 1 <= policy["min_approvals"] <= 5:
        raise ValueError("min_approvals 范围为 1–5")
    if type(policy["max_replicas"]) is not int or not 1 <= policy["max_replicas"] <= 1000:
        raise ValueError("max_replicas 范围为 1–1000")
    if type(policy["rollout_timeout"]) is not int or not 30 <= policy["rollout_timeout"] <= 3600:
        raise ValueError("rollout_timeout 范围为 30–3600")
    orgs = policy["allowed_registry_orgs"]
    if not isinstance(orgs, list) or any(not isinstance(x, str) or not ORG_RE.fullmatch(x) for x in orgs):
        raise ValueError("allowed_registry_orgs 无效")
    types = policy["service_types"]
    if not isinstance(types, list) or not types or any(x not in {"ClusterIP", "NodePort", "LoadBalancer"} for x in types):
        raise ValueError("service_types 无效")
    secrets = policy["image_pull_secrets"]
    if not isinstance(secrets, list) or any(not isinstance(x, str) or not DNS_LABEL_RE.fullmatch(x) for x in secrets):
        raise ValueError("image_pull_secrets 无效")
    for key in _NESTED_DEFAULTS:
        if not isinstance(policy[key], dict):
            raise ValueError(f"{key} 必须是对象")
        policy[key] = {**DEFAULT_POLICY[key], **policy[key]}
    _check_ingress(policy["ingress"])
    _check_resources(policy["resources"])
    _check_freeze(policy["freeze"])
    _check_windows(policy["deploy_windows"])
    return policy


def _check_ingress(ingress: Dict[str, Any]) -> None:
    if set(ingress) - {"enabled", "host_suffixes", "ingress_class"}:
        raise ValueError("ingress 策略字段无效")
    if not isinstance(ingress["enabled"], bool):
        raise ValueError("ingress.enabled 必须为布尔值")
    suffixes = ingress["host_suffixes"]
    if not isinstance(suffixes, list) or any(not isinstance(x, str) or len(x) > 254 or not _DNS_SUFFIX_RE.fullmatch(x)
                                             for x in suffixes):
        raise ValueError("ingress.host_suffixes 必须是带前导点的 DNS 后缀")
    if not isinstance(ingress["ingress_class"], str) or len(ingress["ingress_class"]) > 253:
        raise ValueError("ingress_class 无效")


def _check_resources(resources: Dict[str, Any]) -> None:
    if set(resources) - {"cpu_request_max", "cpu_limit_max", "memory_request_max", "memory_limit_max", "require_limits"}:
        raise ValueError("resources 策略字段无效")
    if not isinstance(resources["require_limits"], bool):
        raise ValueError("resources.require_limits 必须为布尔值")
    for key, value in resources.items():
        if key != "require_limits" and (not isinstance(value, str) or not _QUANTITY_RE.fullmatch(value)):
            raise ValueError(f"resources.{key} Kubernetes quantity 无效")


def _check_freeze(freeze: Dict[str, Any]) -> None:
    if set(freeze) - {"enabled", "until", "reason"} or not isinstance(freeze["enabled"], bool):
        raise ValueError("freeze 策略字段无效")
    if freeze["until"] is not None:
        if not isinstance(freeze["until"], str):
            raise ValueError("freeze.until 必须是 ISO 8601 时间")
        try:
            until = datetime.fromisoformat(freeze["until"].replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("freeze.until 必须是 ISO 8601 时间") from None
        if until.tzinfo is None:
            raise ValueError("freeze.until 必须包含时区")
    if not isinstance(freeze["reason"], str) or len(freeze["reason"]) > 300:
        raise ValueError("freeze.reason 最多 300 字符")


def _check_windows(windows: Any) -> None:
    if not isinstance(windows, list):
        raise ValueError("deploy_windows 必须是数组")
    for window in windows:
        if not isinstance(window, dict) or set(window) != {"days", "start", "end", "tz"}:
            raise ValueError("deploy_windows 项必须包含 days/start/end/tz")
        days = window["days"]
        if not isinstance(days, list) or not days or any(type(day) is not int or not 1 <= day <= 7 for day in days):
            raise ValueError("deploy_windows.days 必须为 ISO 星期编号 1–7 的非空数组")
        if any(not isinstance(window[key], str) or not _HHMM_RE.fullmatch(window[key]) for key in ("start", "end")):
            raise ValueError("deploy_windows start/end 必须为 HH:MM")
        if window["start"] == window["end"]:
            raise ValueError("deploy_windows.start 与 end 不能相同")
        try:
            ZoneInfo(window["tz"])
        except (ZoneInfoNotFoundError, TypeError, ValueError):
            raise ValueError("deploy_windows.tz 必须是有效 IANA 时区") from None


_CRUD = ("get", "list", "watch", "create", "patch", "update", "delete")


def rbac_checks(policy: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    """平台在命名空间内需要的 (API group, resource, verb) 列表。"""
    checks = [("apps", "deployments", verb) for verb in _CRUD]
    checks += [("", resource, verb) for resource in ("services", "configmaps") for verb in _CRUD]
    checks += [("", resource, verb) for resource in ("pods", "events") for verb in ("get", "list", "watch")]
    checks += [("", "pods/log", "get"), ("", "secrets", "create"), ("", "secrets", "patch"), ("", "secrets", "update")]
    if policy.get("hpa_enabled"):
        checks += [("autoscaling", "horizontalpodautoscalers", verb) for verb in _CRUD]
    if policy.get("ingress", {}).get("enabled"):
        checks += [("networking.k8s.io", "ingresses", verb) for verb in _CRUD]
    return checks


def rbac_yaml(namespace: str) -> str:
    return ("apiVersion: rbac.authorization.k8s.io/v1\nkind: Role\nmetadata:\n  name: swr-builder-deployer\n"
            f"  namespace: {namespace}\nrules:\n  - apiGroups: [\"apps\"]\n    resources: [\"deployments\"]\n"
            "    verbs: [\"get\", \"list\", \"watch\", \"create\", \"patch\", \"update\", \"delete\"]\n"
            "  - apiGroups: [\"\"]\n    resources: [\"services\", \"configmaps\"]\n"
            "    verbs: [\"get\", \"list\", \"watch\", \"create\", \"patch\", \"update\", \"delete\"]\n"
            "  - apiGroups: [\"\"]\n    resources: [\"pods\", \"events\"]\n    verbs: [\"get\", \"list\", \"watch\"]\n"
            "  - apiGroups: [\"\"]\n    resources: [\"pods/log\"]\n    verbs: [\"get\"]\n")
