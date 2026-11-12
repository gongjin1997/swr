"""两阶段渲染系统：参数装配 → values.yaml → K8s 对象模板

基于 XAC 设计理念的两阶段渲染：
1. 阶段 1: 参数装配（五级覆盖）→ 渲染 values.yaml
2. 阶段 2: 使用 values 作为最高优先级 → 渲染各个 K8s 对象模板

参数装配五级覆盖：
1. Feature 参数（最低优先级）
2. 资源参数
3. 服务属性参数
4. 配置参数（三层覆盖）
5. 证书参数（最高优先级）
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

import yaml
from jinja2 import Environment, FileSystemLoader, Template

from ..store import Store
from ..store.enhanced_config import ConfigStage, resolve_config


class RenderContext:
    """渲染上下文"""

    def __init__(
        self,
        service_id: int,
        image_digest: str,
        deployment_id: int,
    ):
        self.service_id = service_id
        self.image_digest = image_digest
        self.deployment_id = deployment_id
        self.params: Dict[str, Any] = {}
        self.values: Dict[str, Any] = {}


class TwoStageRenderer:
    """两阶段渲染器"""

    def __init__(self, store: Store, template_dir: str = "templates"):
        self.store = store
        self.env = Environment(
            loader=FileSystemLoader(template_dir),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def render(
        self,
        service_id: int,
        image_digest: str,
        deployment_id: int,
        stage: str = ConfigStage.APP,
    ) -> List[Dict[str, Any]]:
        """执行两阶段渲染

        Returns:
            K8s 对象列表
        """
        # 创建渲染上下文
        ctx = RenderContext(
            service_id=service_id,
            image_digest=image_digest,
            deployment_id=deployment_id,
        )

        # ===== 阶段 1: 参数装配 =====
        ctx.params = self._assemble_params(service_id, stage)

        # ===== 阶段 1.5: 渲染 values.yaml =====
        ctx.values = self._render_values(ctx)

        # ===== 阶段 2: 渲染 K8s 对象 =====
        manifests = self._render_manifests(ctx)

        return manifests

    def _assemble_params(
        self,
        service_id: int,
        stage: str,
    ) -> Dict[str, Any]:
        """参数装配（五级覆盖）

        1. Feature 参数（最低优先级）
        2. 资源参数
        3. 服务属性参数
        4. 配置参数（已经是三层覆盖后的结果）
        5. 证书参数（最高优先级）
        """
        params: Dict[str, Any] = {}

        # 1. Feature 参数（最低优先级）
        params.update(self._get_feature_params())

        # 2. 资源参数
        params.update(self._get_resource_params(service_id))

        # 3. 服务属性参数
        params.update(self._get_service_attributes(service_id))

        # 4. 配置参数（已经是平台 > 项目 > 环境 > 服务的覆盖结果）
        config = resolve_config(self.store, service_id, stage)
        params.update(self._config_to_params(config))

        # 5. 证书参数（最高优先级）
        params.update(self._get_certificate_params(service_id))

        return params

    def _render_values(self, ctx: RenderContext) -> Dict[str, Any]:
        """渲染 values.yaml"""
        # 加载 values.yaml 模板
        template = self.env.get_template("values.yaml.j2")

        # 渲染
        values_yaml = template.render(**ctx.params)

        # 解析为字典
        values = yaml.safe_load(values_yaml)

        return values

    def _render_manifests(self, ctx: RenderContext) -> List[Dict[str, Any]]:
        """渲染各个 K8s 对象模板"""
        from datetime import datetime, timezone

        manifests = []

        # 准备渲染参数（values 作为最高优先级）
        render_params = {
            "values": ctx.values,
            "deployment_id": ctx.deployment_id,
            "image_digest": ctx.image_digest,
            "restarted_at": datetime.now(timezone.utc).isoformat(),
            "config_hash": self._compute_config_hash(ctx.params.get("config", {})),
            **ctx.params,  # 原始参数作为备用
        }

        # 1. ConfigMap 和 Secret
        from .config_objects import generate_config_objects

        service = ctx.params["service"]
        config_objects = generate_config_objects(
            self.store,
            service_id=ctx.service_id,
            namespace=service["namespace"],
            stage=ctx.params.get("stage", ConfigStage.APP),
        )
        manifests.extend(config_objects.configmaps)
        manifests.extend(config_objects.secrets)

        # 2. Deployment
        deployment_template = self.env.get_template("deployment.yaml.j2")
        deployment_yaml = deployment_template.render(**render_params)
        deployment = yaml.safe_load(deployment_yaml)
        manifests.append(deployment)

        # 3. Service
        service_template = self.env.get_template("service.yaml.j2")
        service_yaml = service_template.render(**render_params)
        service_manifest = yaml.safe_load(service_yaml)
        manifests.append(service_manifest)

        # 4. Ingress（如果启用）
        if ctx.values.get("ingress", {}).get("enabled"):
            ingress_template = self.env.get_template("ingress.yaml.j2")
            ingress_yaml = ingress_template.render(**render_params)
            ingress = yaml.safe_load(ingress_yaml)
            manifests.append(ingress)

        # 5. HPA（如果启用）
        if ctx.values.get("hpa", {}).get("enabled"):
            hpa = self._generate_hpa(ctx.values, service["name"], service["namespace"])
            manifests.append(hpa)

        # 6. PDB（如果启用）
        if ctx.values.get("pdb", {}).get("enabled"):
            pdb = self._generate_pdb(ctx.values, service["name"], service["namespace"])
            manifests.append(pdb)

        # 7. ServiceMonitor（如果启用）
        if ctx.values.get("monitoring", {}).get("enabled"):
            service_monitor = self._generate_service_monitor(
                ctx.values, service["name"], service["namespace"]
            )
            manifests.append(service_monitor)

        return manifests

    # === 参数装配辅助函数 ===

    def _get_feature_params(self) -> Dict[str, Any]:
        """获取 Feature 参数（最低优先级）"""
        return {
            "platform_version": "v2.0",
            "builder_managed": True,
        }

    def _get_resource_params(self, service_id: int) -> Dict[str, Any]:
        """获取资源参数（资源绑定导出）"""
        # 查询服务的资源绑定
        bindings = self.store.execute_all(
            """SELECT srb.*, r.type, r.attributes, r.parent_resource_id
               FROM service_resource_bindings srb
               JOIN resources r ON srb.resource_id = r.id
               WHERE srb.service_id = %s""",
            service_id
        )

        resources = {}
        for binding in bindings:
            # 解析属性
            attributes = json.loads(binding["attributes"]) if isinstance(binding["attributes"], str) else binding["attributes"]

            resources[binding["alias"]] = {
                "type": binding["type"],
                "attributes": attributes,
            }

        return {"resources": resources}

    def _get_service_attributes(self, service_id: int) -> Dict[str, Any]:
        """获取服务属性参数"""
        service = self.store.execute_one(
            "SELECT * FROM services WHERE id = %s",
            service_id
        )

        if not service:
            raise ValueError(f"服务 {service_id} 不存在")

        # 获取命名空间
        namespace = self.store.execute_one(
            "SELECT * FROM namespaces WHERE id = %s",
            service["namespace_id"]
        )

        # 获取项目
        project = self.store.execute_one(
            "SELECT * FROM projects WHERE id = %s",
            service["project_id"]
        )

        # 获取环境档案（profile）
        profile = self._get_environment_profile(service["namespace_id"])

        return {
            "service": {
                "id": service["id"],
                "name": service["name"],
                "app": service.get("app_name") or service["name"],
                "namespace": namespace["name"],
                "namespace_id": namespace["id"],
                "project": project["name"],
                "project_id": project["id"],
                "replicas": service.get("replicas") or profile.get("default_replicas", 2),
                "resources": service.get("resources") or profile.get("default_resources"),
                "ports": service.get("ports") or [],
                "ingress_enabled": service.get("ingress_enabled", False),
                "ingress_host": service.get("ingress_host", ""),
                "ingress_path": service.get("ingress_path", "/"),
                "hpa_enabled": service.get("hpa_enabled", False),
                "hpa_min_replicas": service.get("hpa_min_replicas", 2),
                "hpa_max_replicas": service.get("hpa_max_replicas", 10),
                "hpa_cpu_target": service.get("hpa_cpu_target", 80),
                "hpa_memory_target": service.get("hpa_memory_target"),
                "pdb_enabled": service.get("pdb_enabled", False),
                "pdb_min_available": service.get("pdb_min_available", 1),
                "monitoring_enabled": service.get("monitoring_enabled", False),
                "metrics_port": service.get("metrics_port", 9090),
                "metrics_path": service.get("metrics_path", "/metrics"),
            },
            "environment": {
                "name": namespace["name"],
                "profile": profile,
            },
            "project": {
                "name": project["name"],
            },
        }

    def _config_to_params(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """将配置转换为参数"""
        # 配置已经按优先级解析，直接使用
        config_dict = {}
        for key, value_obj in config.items():
            config_dict[key] = value_obj["value"]

        return {"config": config_dict}

    def _get_certificate_params(self, service_id: int) -> Dict[str, Any]:
        """获取证书参数（最高优先级）"""
        # 查询证书资源绑定
        cert_bindings = self.store.execute_all(
            """SELECT srb.*, r.attributes
               FROM service_resource_bindings srb
               JOIN resources r ON srb.resource_id = r.id
               WHERE srb.service_id = %s AND r.type = 'elb_certificate'""",
            service_id
        )

        certificates = {}
        for binding in cert_bindings:
            attributes = json.loads(binding["attributes"]) if isinstance(binding["attributes"], str) else binding["attributes"]
            certificates[binding["alias"]] = attributes

        return {"certificates": certificates}

    def _get_environment_profile(self, namespace_id: int) -> Dict[str, Any]:
        """获取环境档案（命名规则、调度、资源限制等）"""
        # TODO: 从数据库读取环境档案
        # 这里返回默认值
        return {
            "default_replicas": 2,
            "default_resources": {
                "requests": {
                    "cpu": "100m",
                    "memory": "128Mi",
                },
                "limits": {
                    "cpu": "1000m",
                    "memory": "512Mi",
                },
            },
            "rollout_strategy": {
                "maxSurge": 1,
                "maxUnavailable": 0,
            },
            "node_selector": {},
            "tolerations": [],
            "affinity": {},
            "default_liveness_probe": {
                "path": "/health",
                "port": 8080,
                "initialDelaySeconds": 30,
                "periodSeconds": 10,
                "timeoutSeconds": 5,
                "failureThreshold": 3,
            },
            "default_readiness_probe": {
                "path": "/ready",
                "port": 8080,
                "initialDelaySeconds": 10,
                "periodSeconds": 5,
                "timeoutSeconds": 3,
                "failureThreshold": 3,
            },
        }

    # === K8s 对象生成辅助函数 ===

    def _generate_hpa(
        self,
        values: Dict[str, Any],
        service_name: str,
        namespace: str,
    ) -> Dict[str, Any]:
        """生成 HPA 对象"""
        hpa_config = values.get("hpa", {})

        hpa = {
            "apiVersion": "autoscaling/v2",
            "kind": "HorizontalPodAutoscaler",
            "metadata": {
                "name": f"{service_name}-hpa",
                "namespace": namespace,
                "labels": {
                    "app": service_name,
                    "managed-by": "swr-builder",
                },
            },
            "spec": {
                "scaleTargetRef": {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": service_name,
                },
                "minReplicas": hpa_config.get("minReplicas", 2),
                "maxReplicas": hpa_config.get("maxReplicas", 10),
                "metrics": [],
                "behavior": {
                    "scaleDown": {
                        "stabilizationWindowSeconds": 300,
                        "policies": [
                            {"type": "Percent", "value": 50, "periodSeconds": 60},
                            {"type": "Pods", "value": 2, "periodSeconds": 60},
                        ],
                        "selectPolicy": "Min",
                    },
                    "scaleUp": {
                        "stabilizationWindowSeconds": 0,
                        "policies": [
                            {"type": "Percent", "value": 100, "periodSeconds": 15},
                            {"type": "Pods", "value": 4, "periodSeconds": 15},
                        ],
                        "selectPolicy": "Max",
                    },
                },
            },
        }

        # CPU 指标
        if hpa_config.get("targetCPU"):
            hpa["spec"]["metrics"].append({
                "type": "Resource",
                "resource": {
                    "name": "cpu",
                    "target": {
                        "type": "Utilization",
                        "averageUtilization": hpa_config["targetCPU"],
                    },
                },
            })

        # 内存指标
        if hpa_config.get("targetMemory"):
            hpa["spec"]["metrics"].append({
                "type": "Resource",
                "resource": {
                    "name": "memory",
                    "target": {
                        "type": "Utilization",
                        "averageUtilization": hpa_config["targetMemory"],
                    },
                },
            })

        return hpa

    def _generate_pdb(
        self,
        values: Dict[str, Any],
        service_name: str,
        namespace: str,
    ) -> Dict[str, Any]:
        """生成 PDB 对象"""
        pdb_config = values.get("pdb", {})

        return {
            "apiVersion": "policy/v1",
            "kind": "PodDisruptionBudget",
            "metadata": {
                "name": f"{service_name}-pdb",
                "namespace": namespace,
                "labels": {
                    "app": service_name,
                    "managed-by": "swr-builder",
                },
            },
            "spec": {
                "minAvailable": pdb_config.get("minAvailable", 1),
                "selector": {
                    "matchLabels": {
                        "app": service_name,
                    },
                },
            },
        }

    def _generate_service_monitor(
        self,
        values: Dict[str, Any],
        service_name: str,
        namespace: str,
    ) -> Dict[str, Any]:
        """生成 ServiceMonitor 对象（Prometheus）"""
        monitoring_config = values.get("monitoring", {})

        return {
            "apiVersion": "monitoring.coreos.com/v1",
            "kind": "ServiceMonitor",
            "metadata": {
                "name": service_name,
                "namespace": namespace,
                "labels": {
                    "app": service_name,
                    "managed-by": "swr-builder",
                    "monitoring": "prometheus",
                },
            },
            "spec": {
                "selector": {
                    "matchLabels": {
                        "app": service_name,
                    },
                },
                "endpoints": [
                    {
                        "port": monitoring_config.get("portName", "metrics"),
                        "path": monitoring_config.get("path", "/metrics"),
                        "interval": monitoring_config.get("interval", "30s"),
                    },
                ],
            },
        }

    # === 辅助函数 ===

    def _compute_config_hash(self, config: Dict[str, Any]) -> str:
        """计算配置哈希"""
        config_str = json.dumps(config, sort_keys=True)
        return hashlib.sha256(config_str.encode()).hexdigest()[:16]
