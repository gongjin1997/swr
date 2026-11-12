"""适配层：将现有 Store 数据适配到新的领域模型。

用于渐进式重构：保持现有 API 不变，内部使用新的领域模型和 ParamResolver。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..core.domain import ConfigEntry, ConfigLayer, DeploymentUnit, Resource, ResourceBinding
from ..core.param_resolver import ParamResolver, ResolvedParams
from ..store import Store


class DomainAdapter:
    """将 Store 数据适配到领域模型"""

    def __init__(self, store: Store):
        self.store = store

    def build_deployment_unit(self, service: Dict[str, Any], spec: Dict[str, Any]) -> DeploymentUnit:
        """
        从现有 service 记录构建 DeploymentUnit。

        Args:
            service: store 中的 service 记录
            spec: 服务规格

        Returns:
            DeploymentUnit 领域对象
        """
        # 加载资源绑定
        bindings = self._load_resource_bindings(service["id"])

        # 加载配置层
        config_layers = self._load_config_layers(service)

        return DeploymentUnit(
            id=service["id"],
            application_id=service.get("app_id", 0),
            name=service["name"],
            namespace_id=service["namespace_id"],
            spec=spec,
            attributes={},  # DU 自身属性（暂时为空，未来从 spec 中提取）
            resource_bindings=bindings,
            config_layers=config_layers,
        )

    def _load_resource_bindings(self, service_id: int) -> List[ResourceBinding]:
        """加载服务的资源绑定"""
        bindings_data = self.store.list_bindings(service_id)
        bindings = []

        for binding_data in bindings_data:
            resource_data = self.store.get_resource(binding_data["resource_id"])
            if not resource_data:
                continue

            # 构建 Resource 对象
            resource = Resource(
                id=resource_data["id"],
                type=resource_data["type"],
                name=resource_data["name"],
                scope_type=resource_data["scope_type"],
                scope_id=resource_data["scope_id"],
                parent_id=resource_data.get("parent_id"),
                attributes=resource_data["attributes"],
                version=resource_data.get("current_version", 1),
            )

            binding = ResourceBinding(
                du_id=service_id,
                resource_id=binding_data["resource_id"],
                alias=binding_data.get("alias", ""),
                resource=resource,
            )
            bindings.append(binding)

        return bindings

    def _load_config_layers(self, service: Dict[str, Any]) -> List[ConfigLayer]:
        """加载配置层（platform → project → environment → service）"""
        from ..delivery.resolve import layers

        config_layers = []
        layer_scopes = layers(service)

        for scope_type, scope_id in layer_scopes:
            sets = self.store.list_config_sets([(scope_type, scope_id)])

            for config_set in sets:
                if not config_set.get("version"):
                    continue

                version_data = self.store.get_config_version(config_set["id"], config_set["version"])
                if not version_data:
                    continue

                # 转换为 ConfigEntry
                entries = {}
                for entry_data in version_data["entries"]:
                    entry = ConfigEntry(
                        key=entry_data["key"],
                        value=entry_data.get("value"),
                        locked=entry_data.get("locked", False),
                        secret=config_set["kind"] in ("secret_env", "secret_files"),
                    )
                    entries[entry.key] = entry

                config_layer = ConfigLayer(
                    scope_type=scope_type,
                    scope_id=scope_id,
                    config_type=config_set["kind"],
                    entries=entries,
                    version=config_set["version"],
                )
                config_layers.append(config_layer)

        return config_layers


def resolve_with_param_resolver(
    store: Store,
    service: Dict[str, Any],
    spec: Dict[str, Any],
    artifact: Optional[Dict[str, Any]] = None,
    external_params: Optional[Dict[str, Any]] = None,
    required_params: Optional[List[str]] = None,
) -> ResolvedParams:
    """
    使用新的 ParamResolver 解析参数。

    这是适配函数，用于在现有代码中使用新的参数解析器。
    """
    adapter = DomainAdapter(store)
    du = adapter.build_deployment_unit(service, spec)

    resolver = ParamResolver()
    return resolver.resolve(
        du=du,
        artifact=artifact,
        external_params=external_params,
        required_params=required_params,
    )
