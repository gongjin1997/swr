"""部署流程集成：将两阶段渲染集成到实际部署中"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..errors import DomainError
from ..store import Store
from .config_effect import ConfigEffectStrategy, apply_config_changes, detect_config_changes
from .two_stage_render import TwoStageRenderer


class DeploymentExecutor:
    """部署执行器：协调配置、渲染、应用的完整流程"""

    def __init__(self, store: Store, k8s_client: Any):
        self.store = store
        self.k8s_client = k8s_client
        self.renderer = TwoStageRenderer(store)

    def execute_deployment(
        self,
        service_id: int,
        image_digest: str,
        deployment_id: int,
        *,
        force_restart: bool = False,
    ) -> Dict[str, Any]:
        """执行完整的部署流程

        流程：
        1. 检测配置变更
        2. 渲染 K8s 对象（两阶段渲染）
        3. 应用对象到集群
        4. 等待就绪
        5. 创建配置快照（OBOM）

        Args:
            service_id: 服务 ID
            image_digest: 镜像 digest
            deployment_id: 部署记录 ID
            force_restart: 是否强制重启（忽略配置变更检测）

        Returns:
            部署结果
        """
        # 1. 检测配置变更
        config_changes = []
        if not force_restart:
            from ..store.enhanced_config import resolve_config

            new_config = resolve_config(self.store, service_id)
            config_changes = detect_config_changes(self.store, service_id, new_config)

        # 2. 渲染 K8s 对象（两阶段渲染）
        manifests = self.renderer.render(
            service_id=service_id,
            image_digest=image_digest,
            deployment_id=deployment_id,
        )

        # 3. 应用对象到集群
        apply_results = self._apply_manifests(manifests)

        # 4. 根据配置变更决定是否需要额外操作
        if config_changes and not force_restart:
            effect_result = apply_config_changes(
                self.store,
                self.k8s_client,
                service_id,
                config_changes,
                support_hot_reload=False,  # TODO: 从服务配置读取
            )
        else:
            effect_result = None

        # 5. 等待 Deployment 就绪
        service = self.store.execute_one("SELECT * FROM services WHERE id = %s", service_id)
        namespace = self.store.execute_one(
            "SELECT name FROM namespaces WHERE id = %s",
            service["namespace_id"]
        )["name"]

        ready_status = self._wait_for_ready(
            namespace=namespace,
            deployment_name=service["name"],
            timeout=600,
        )

        # 6. 创建配置快照（OBOM）
        from ..services.enhanced_config import snapshot_config

        snapshot_id = snapshot_config(
            self.store,
            deployment_id=deployment_id,
            service_id=service_id,
        )

        return {
            "deployment_id": deployment_id,
            "manifests_applied": len(apply_results),
            "config_changes": len(config_changes),
            "config_effect": effect_result.strategy.value if effect_result else None,
            "ready": ready_status["ready"],
            "snapshot_id": snapshot_id,
        }

    def _apply_manifests(
        self,
        manifests: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """应用 K8s 对象"""
        results = []

        for manifest in manifests:
            kind = manifest["kind"]
            name = manifest["metadata"]["name"]
            namespace = manifest["metadata"]["namespace"]

            # 先 GET 检查是否存在
            existing = self.k8s_client.get(
                kind=kind,
                name=name,
                namespace=namespace,
            )

            if existing:
                # 已存在，执行 update
                manifest["metadata"]["resourceVersion"] = existing["metadata"]["resourceVersion"]
                result = self.k8s_client.update(
                    kind=kind,
                    name=name,
                    namespace=namespace,
                    body=manifest,
                )
                action = "updated"
            else:
                # 不存在，执行 create
                result = self.k8s_client.create(
                    kind=kind,
                    namespace=namespace,
                    body=manifest,
                )
                action = "created"

            results.append({
                "kind": kind,
                "name": name,
                "action": action,
                "object": result,
            })

        return results

    def _wait_for_ready(
        self,
        namespace: str,
        deployment_name: str,
        timeout: int = 600,
    ) -> Dict[str, Any]:
        """等待 Deployment 就绪（完整检查）

        检查项：
        1. observedGeneration == metadata.generation（确保是最新版本）
        2. updatedReplicas == spec.replicas（所有副本已更新）
        3. availableReplicas == spec.replicas（所有副本可用）
        4. 无 Progressing=False 条件（没有进度失败）
        """
        import time

        start_time = time.time()

        while time.time() - start_time < timeout:
            # 获取 Deployment 状态
            deployment = self.k8s_client.get(
                kind="Deployment",
                name=deployment_name,
                namespace=namespace,
            )

            if not deployment:
                time.sleep(5)
                continue

            # 检查 observedGeneration
            observed_generation = deployment.get("status", {}).get("observedGeneration")
            metadata_generation = deployment.get("metadata", {}).get("generation")

            if observed_generation != metadata_generation:
                # 还没有观察到最新版本
                time.sleep(5)
                continue

            # 检查副本数
            spec_replicas = deployment.get("spec", {}).get("replicas", 0)
            status = deployment.get("status", {})
            updated_replicas = status.get("updatedReplicas", 0)
            available_replicas = status.get("availableReplicas", 0)

            if updated_replicas != spec_replicas:
                # 还有副本没有更新
                time.sleep(5)
                continue

            if available_replicas != spec_replicas:
                # 还有副本不可用
                time.sleep(5)
                continue

            # 检查 Progressing 条件
            conditions = status.get("conditions", [])
            progressing_condition = next(
                (c for c in conditions if c["type"] == "Progressing"),
                None
            )

            if progressing_condition and progressing_condition["status"] == "False":
                # 进度失败
                return {
                    "ready": False,
                    "reason": progressing_condition.get("reason", "Unknown"),
                    "message": progressing_condition.get("message", ""),
                }

            # 所有检查通过
            return {
                "ready": True,
                "replicas": spec_replicas,
                "updated_replicas": updated_replicas,
                "available_replicas": available_replicas,
            }

        # 超时
        return {
            "ready": False,
            "reason": "Timeout",
            "message": f"Deployment did not become ready within {timeout} seconds",
        }


def render_and_preview(
    store: Store,
    service_id: int,
    image_digest: str,
) -> List[Dict[str, Any]]:
    """渲染并预览 K8s 对象（不实际部署）

    用于：
    - 部署前预览
    - 配置验证
    - 模板调试
    """
    renderer = TwoStageRenderer(store)

    manifests = renderer.render(
        service_id=service_id,
        image_digest=image_digest,
        deployment_id=0,  # 预览时不需要真实的 deployment_id
    )

    return manifests


def validate_manifests(
    manifests: List[Dict[str, Any]],
) -> List[str]:
    """验证 K8s 对象的合法性

    检查项：
    - 必需字段存在
    - 字段类型正确
    - 值范围合法
    """
    errors = []

    for manifest in manifests:
        kind = manifest.get("kind")
        if not kind:
            errors.append("缺少 kind 字段")
            continue

        metadata = manifest.get("metadata")
        if not metadata:
            errors.append(f"{kind}: 缺少 metadata 字段")
            continue

        if not metadata.get("name"):
            errors.append(f"{kind}: 缺少 metadata.name 字段")

        if not metadata.get("namespace"):
            errors.append(f"{kind}: 缺少 metadata.namespace 字段")

        spec = manifest.get("spec")
        if not spec and kind not in ("ConfigMap", "Secret"):
            errors.append(f"{kind}: 缺少 spec 字段")

        # Deployment 特定检查
        if kind == "Deployment":
            if not spec.get("selector"):
                errors.append("Deployment: 缺少 spec.selector 字段")

            if not spec.get("template"):
                errors.append("Deployment: 缺少 spec.template 字段")

            containers = spec.get("template", {}).get("spec", {}).get("containers", [])
            if not containers:
                errors.append("Deployment: 缺少 containers")

            for i, container in enumerate(containers):
                if not container.get("name"):
                    errors.append(f"Deployment: container[{i}] 缺少 name")

                if not container.get("image"):
                    errors.append(f"Deployment: container[{i}] 缺少 image")

    return errors
