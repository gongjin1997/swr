"""参数解析器：实现 XAC 的 getParams 逻辑。

优先级（从低到高）：
1. feature 层（平台特性默认值）
2. resource 层（绑定的资源导出）
3. DU attributes（DU 自身属性）
4. artifact placeholders（制品中的占位符）
5. certificate（证书引用）
6. externalParams（外部传入参数）

特殊规则：
- 锁定的 key 不能被下层覆盖
- 必填参数缺失时记录错误
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .domain import ConfigLayer, DeploymentUnit, Resource, ResourceBinding


@dataclass
class ParamSource:
    """参数来源追踪"""
    key: str
    value: Any
    source_layer: str  # feature, resource, du_attr, artifact, cert, external
    source_type: str  # config, resource, attribute
    source_id: Optional[int]
    source_name: str
    scope: str  # platform, project, environment, du
    version: int
    locked: bool
    overridden: bool = False
    overridden_by: Optional[str] = None


@dataclass
class ResolvedParams:
    """解析后的参数"""
    params: Dict[str, Any]
    sources: Dict[str, ParamSource]
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class ParamResolver:
    """参数解析器"""

    # 优先级定义（数字越大优先级越高）
    LAYER_PRIORITY = {
        "feature": 1,
        "resource": 2,
        "du_attr": 3,
        "artifact": 4,
        "cert": 5,
        "external": 6,
    }

    SCOPE_PRIORITY = {
        "platform": 1,
        "project": 2,
        "environment": 3,
        "du": 4,
    }

    def resolve(
        self,
        du: DeploymentUnit,
        artifact: Optional[Dict[str, Any]] = None,
        external_params: Optional[Dict[str, Any]] = None,
        required_params: Optional[List[str]] = None,
    ) -> ResolvedParams:
        """
        解析参数，返回最终参数和来源追踪。

        Args:
            du: 部署单元
            artifact: 制品信息（包含 placeholders）
            external_params: 外部传入参数
            required_params: 必填参数列表

        Returns:
            ResolvedParams: 包含最终参数、来源追踪、警告和错误
        """
        result_params: Dict[str, Any] = {}
        result_sources: Dict[str, ParamSource] = {}
        warnings: List[str] = []
        errors: List[str] = []

        # 1. feature 层（平台默认值）
        self._merge_layer(
            result_params, result_sources, warnings,
            layer="feature", scope="platform",
            entries={}, source_type="config"
        )

        # 2. resource 层（绑定的资源导出）
        for binding in du.resource_bindings:
            if binding.resource:
                # 使用资源导出引擎
                from .resource_exporter import ResourceExporter
                exporter = ResourceExporter()

                # 构建父链（用于属性继承）
                parent_chain = getattr(binding.resource, 'parent_chain', [])

                exported = exporter.export_resource(
                    {
                        "type": binding.resource.type,
                        "name": binding.resource.name,
                        "attributes": binding.resource.attributes,
                    },
                    parent_chain
                )

                # 合并导出的配置（env 和 secret_env）
                for kind in ["env", "secret_env"]:
                    for entry in exported.get(kind, []):
                        resource_entries = {
                            entry["key"]: {
                                "key": entry["key"],
                                "value": entry["value"],
                                "locked": False,
                            }
                        }
                        self._merge_layer(
                            result_params, result_sources, warnings,
                            layer="resource", scope="environment",
                            entries=resource_entries,
                            source_type="resource",
                            source_id=binding.resource_id,
                            source_name=binding.resource.name,
                            version=binding.resource.version
                        )

        # 3. config 层（platform < project < environment < du）
        for config_layer in sorted(du.config_layers, key=lambda c: self.SCOPE_PRIORITY.get(c.scope_type, 0)):
            self._merge_layer(
                result_params, result_sources, warnings,
                layer="feature",  # config 也算 feature 层
                scope=config_layer.scope_type,
                entries={e.key: e for e in config_layer.entries.values()},
                source_type="config",
                source_id=config_layer.scope_id,
                source_name=f"{config_layer.scope_type}_{config_layer.config_type}",
                version=config_layer.version
            )

        # 4. DU attributes
        if du.attributes:
            du_entries = {k: {"key": k, "value": v, "locked": False} for k, v in du.attributes.items()}
            self._merge_layer(
                result_params, result_sources, warnings,
                layer="du_attr", scope="du",
                entries=du_entries,
                source_type="attribute",
                source_id=du.id,
                source_name=du.name,
                version=1
            )

        # 5. artifact placeholders
        if artifact and artifact.get("placeholders"):
            placeholder_entries = {k: {"key": k, "value": v, "locked": False}
                                   for k, v in artifact["placeholders"].items()}
            self._merge_layer(
                result_params, result_sources, warnings,
                layer="artifact", scope="du",
                entries=placeholder_entries,
                source_type="artifact",
                source_id=artifact.get("id"),
                source_name=artifact.get("name", "artifact"),
                version=artifact.get("version", 1)
            )

        # 6. external params（最高优先级）
        if external_params:
            external_entries = {k: {"key": k, "value": v, "locked": False}
                                for k, v in external_params.items()}
            self._merge_layer(
                result_params, result_sources, warnings,
                layer="external", scope="du",
                entries=external_entries,
                source_type="external",
                source_id=None,
                source_name="external",
                version=1
            )

        # 检查必填参数
        if required_params:
            missing = [key for key in required_params if key not in result_params]
            if missing:
                errors.append(f"缺少必填参数: {', '.join(missing[:5])}" + ("..." if len(missing) > 5 else ""))

        return ResolvedParams(
            params=result_params,
            sources=result_sources,
            warnings=warnings,
            errors=errors
        )

    def _merge_layer(
        self,
        result_params: Dict[str, Any],
        result_sources: Dict[str, ParamSource],
        warnings: List[str],
        layer: str,
        scope: str,
        entries: Dict[str, Any],
        source_type: str,
        source_id: Optional[int] = None,
        source_name: str = "",
        version: int = 1,
    ) -> None:
        """合并一层参数"""
        layer_priority = self.LAYER_PRIORITY.get(layer, 0)

        for key, entry in entries.items():
            if isinstance(entry, dict):
                value = entry.get("value")
                locked = entry.get("locked", False)
            else:
                value = entry.value if hasattr(entry, "value") else entry
                locked = entry.locked if hasattr(entry, "locked") else False

            # 检查是否已被锁定
            if key in result_sources:
                existing_source = result_sources[key]
                if existing_source.locked:
                    # 已被锁定，不能覆盖
                    warnings.append(f"{key} 已被 {existing_source.scope} 层锁定，{scope} 层的值未生效")
                    continue

                # 比较优先级
                existing_priority = self.LAYER_PRIORITY.get(existing_source.source_layer, 0)
                if layer_priority <= existing_priority:
                    # 优先级不够，不覆盖
                    continue

                # 标记原来的为被覆盖
                existing_source.overridden = True
                existing_source.overridden_by = f"{scope}/{layer}"

            # 更新参数
            result_params[key] = value
            result_sources[key] = ParamSource(
                key=key,
                value=value,
                source_layer=layer,
                source_type=source_type,
                source_id=source_id,
                source_name=source_name,
                scope=scope,
                version=version,
                locked=locked,
            )

    def _export_resource(self, resource: Resource) -> Dict[str, Any]:
        """导出资源属性为参数"""
        # 简化版：直接返回属性，实际应该根据 export_rules 转换
        return resource.attributes or {}
