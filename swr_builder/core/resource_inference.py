"""资源推断引擎：从 K8s YAML 和 ConfigMap 中推断资源类型和绑定关系。

分析规则：
1. 从环境变量 key 推断资源类型（如 REDIS_HOST → redis）
2. 从 ConfigMap 中的 properties 推断 GaussDB、Redis、Kafka 等
3. 只报告形状，不创建资源
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Set, Tuple


class ResourceInferenceEngine:
    """资源推断引擎"""

    # 推断规则：环境变量 key 模式 → 资源类型
    ENV_PATTERNS = {
        "gauss_db": [
            r"^spring\.datasource\.gauss\.(host|port)$",
            r"^GAUSS_.*",
        ],
        "redis": [
            r"^REDIS_(HOST|PORT|PASSWORD)$",
            r"^spring\.redis\.(host|port)$",
        ],
        "kafka": [
            r"^KAFKA_BROKERS$",
            r"^spring\.kafka\.bootstrap-servers$",
        ],
        "mqs": [
            r"^erpmq\.mqs\.(nameserver|accesskey|secretkey)$",
        ],
        "config_center": [
            r"^configcenter_url$",
        ],
        "iam_endpoint": [
            r"^service_(authentication|authorization)_url$",
            r"^iam\.(endpoint|account)$",
        ],
    }

    # 推断规则：ConfigMap key 组合 → 资源类型
    CONFIG_PATTERNS = {
        "gauss_db": {
            "required": ["spring.datasource.gauss.host", "spring.datasource.gauss.port"],
            "optional": ["spring.datasource.gauss.database", "spring.datasource.gauss.schema"],
        },
        "redis": {
            "required": ["spring.redis.host", "spring.redis.port"],
            "optional": ["REDIS_PASSWORD"],
        },
        "kafka": {
            "required": ["spring.kafka.bootstrap-servers"],
            "optional": [],
        },
    }

    def infer_from_deployment(self, deployment: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        从 Deployment YAML 推断资源绑定。

        Returns:
            推断结果列表：[{type, confidence, evidence, suggested_attributes}]
        """
        inferences = []
        evidence_by_type: Dict[str, Set[str]] = {}

        # 提取环境变量
        spec = deployment.get("spec", {})
        template = spec.get("template", {})
        pod_spec = template.get("spec", {})
        containers = pod_spec.get("containers", [])

        for container in containers:
            # 1. 直接定义的环境变量
            env_vars = container.get("env", [])
            for env in env_vars:
                key = env.get("name", "")
                self._check_env_key(key, evidence_by_type)

            # 2. envFrom 引用的 ConfigMap 和 Secret
            env_from = container.get("envFrom", [])
            for ref in env_from:
                if "configMapRef" in ref:
                    cm_name = ref["configMapRef"].get("name", "")
                    evidence_by_type.setdefault("_configmap", set()).add(cm_name)
                if "secretRef" in ref:
                    secret_name = ref["secretRef"].get("name", "")
                    evidence_by_type.setdefault("_secret", set()).add(secret_name)

        # 转换为推断结果
        for resource_type, evidence_keys in evidence_by_type.items():
            if resource_type.startswith("_"):
                continue  # 跳过内部标记

            inferences.append({
                "type": resource_type,
                "confidence": self._calculate_confidence(resource_type, evidence_keys),
                "evidence": list(evidence_keys),
                "suggested_attributes": self._suggest_attributes(resource_type, evidence_keys),
            })

        return inferences

    def infer_from_configmap(self, configmap: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        从 ConfigMap 推断资源类型。

        Args:
            configmap: ConfigMap 对象

        Returns:
            推断结果列表
        """
        inferences = []
        data = configmap.get("data", {})
        evidence_by_type: Dict[str, Set[str]] = {}

        # 检查所有 key
        for key in data.keys():
            self._check_env_key(key, evidence_by_type)

        # 检查组合模式
        all_keys = set(data.keys())
        for resource_type, patterns in self.CONFIG_PATTERNS.items():
            required = set(patterns["required"])
            if required.issubset(all_keys):
                evidence_by_type.setdefault(resource_type, set()).update(required)

        # 转换为推断结果
        for resource_type, evidence_keys in evidence_by_type.items():
            inferences.append({
                "type": resource_type,
                "confidence": self._calculate_confidence(resource_type, evidence_keys),
                "evidence": list(evidence_keys),
                "suggested_attributes": self._suggest_attributes(resource_type, evidence_keys),
                "source": "configmap",
            })

        return inferences

    def _check_env_key(self, key: str, evidence_by_type: Dict[str, Set[str]]) -> None:
        """检查环境变量 key 是否匹配资源类型模式"""
        for resource_type, patterns in self.ENV_PATTERNS.items():
            for pattern in patterns:
                if re.match(pattern, key):
                    evidence_by_type.setdefault(resource_type, set()).add(key)
                    break

    def _calculate_confidence(self, resource_type: str, evidence_keys: Set[str]) -> float:
        """计算置信度（0-1）"""
        # 简单规则：证据越多，置信度越高
        evidence_count = len(evidence_keys)
        if evidence_count >= 3:
            return 0.9
        elif evidence_count == 2:
            return 0.7
        elif evidence_count == 1:
            return 0.5
        return 0.3

    def _suggest_attributes(self, resource_type: str, evidence_keys: Set[str]) -> Dict[str, str]:
        """根据证据推荐资源属性"""
        suggestions = {}

        if resource_type == "gauss_db":
            if any("host" in k.lower() for k in evidence_keys):
                suggestions["host"] = "待填写"
            if any("port" in k.lower() for k in evidence_keys):
                suggestions["port"] = 5432

        elif resource_type == "redis":
            if any("host" in k.lower() for k in evidence_keys):
                suggestions["host"] = "待填写"
            if any("port" in k.lower() for k in evidence_keys):
                suggestions["port"] = 6379

        elif resource_type == "kafka":
            if "KAFKA_BROKERS" in evidence_keys or "spring.kafka.bootstrap-servers" in evidence_keys:
                suggestions["brokers"] = "待填写"

        return suggestions


def analyze_namespace_yaml(yaml_content: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    分析命名空间导出的 YAML，推断资源需求。

    Args:
        yaml_content: K8s 对象列表

    Returns:
        {
            "deployments": [{name, inferences}],
            "configmaps": [{name, inferences}],
            "summary": {type: count}
        }
    """
    engine = ResourceInferenceEngine()
    result = {
        "deployments": [],
        "configmaps": [],
        "summary": {},
    }

    for obj in yaml_content:
        kind = obj.get("kind")
        metadata = obj.get("metadata", {})
        name = metadata.get("name", "unknown")

        if kind == "Deployment":
            inferences = engine.infer_from_deployment(obj)
            if inferences:
                result["deployments"].append({
                    "name": name,
                    "namespace": metadata.get("namespace"),
                    "inferences": inferences,
                })

        elif kind == "ConfigMap":
            inferences = engine.infer_from_configmap(obj)
            if inferences:
                result["configmaps"].append({
                    "name": name,
                    "namespace": metadata.get("namespace"),
                    "inferences": inferences,
                })

    # 统计汇总
    for deployment in result["deployments"]:
        for inference in deployment["inferences"]:
            resource_type = inference["type"]
            result["summary"][resource_type] = result["summary"].get(resource_type, 0) + 1

    return result
