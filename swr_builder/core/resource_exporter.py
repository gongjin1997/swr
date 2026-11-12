"""资源导出引擎：将资源属性按导出规则转换为配置。

基于 XAC 的资源导出逻辑，支持：
- 属性占位符替换（{attr_name}）
- 按导出规则生成 env、secret_env、files、secret_files
- 父子资源属性继承
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .resource_types import ExportRule, get_resource_type


class ResourceExporter:
    """资源导出引擎"""

    def export_resource(
        self,
        resource: Dict[str, Any],
        parent_chain: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        导出资源为配置条目。

        Args:
            resource: 资源对象（包含 type, attributes）
            parent_chain: 父资源链（用于继承属性）

        Returns:
            {
                "env": [{key, value}],
                "secret_env": [{key, value}],
                "files": [{key, value}],
                "secret_files": [{key, value}],
            }
        """
        resource_type = get_resource_type(resource["type"])
        if not resource_type:
            return {"env": [], "secret_env": [], "files": [], "secret_files": []}

        # 合并属性（自身 + 父链）
        merged_attributes = self._merge_attributes(resource, parent_chain or [])

        # 按导出规则生成配置
        result = {"env": [], "secret_env": [], "files": [], "secret_files": []}

        for rule in resource_type.export_rules:
            try:
                # 替换模板中的占位符
                value = self._render_template(rule.template, merged_attributes)

                result[rule.target_kind].append({
                    "key": rule.key,
                    "value": value,
                    "source": f"resource:{resource['type']}:{resource['name']}",
                })
            except KeyError as e:
                # 属性缺失，跳过该规则
                continue

        return result

    def _merge_attributes(
        self,
        resource: Dict[str, Any],
        parent_chain: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """合并资源属性（父链 → 自身）"""
        merged = {}

        # 从父到子依次合并
        for parent in parent_chain:
            merged.update(parent.get("attributes", {}))

        # 自身属性优先级最高
        merged.update(resource.get("attributes", {}))

        return merged

    def _render_template(self, template: str, attributes: Dict[str, Any]) -> str:
        """
        渲染模板字符串，替换 {attr_name} 占位符。

        不使用 Python 的 str.format() 以避免注入风险，手动替换占位符。
        """
        result = template

        # 查找所有 {xxx} 占位符
        import re
        placeholders = re.findall(r'\{(\w+)\}', template)

        for placeholder in placeholders:
            if placeholder in attributes:
                value = attributes[placeholder]
                # 转换为字符串
                if isinstance(value, bool):
                    value_str = "true" if value else "false"
                elif value is None:
                    value_str = ""
                else:
                    value_str = str(value)

                result = result.replace(f"{{{placeholder}}}", value_str)
            else:
                raise KeyError(f"属性 {placeholder} 不存在")

        return result


def export_resources(
    resources: List[Dict[str, Any]],
    include_secrets: bool = False,
) -> Dict[str, Dict[str, Any]]:
    """
    批量导出资源为配置。

    Args:
        resources: 资源列表（需要已包含父链信息）
        include_secrets: 是否包含敏感值（预览时为 False）

    Returns:
        {
            "env": {key: value},
            "secret_env": {key: value},
            "files": {key: value},
            "secret_files": {key: value},
        }
    """
    exporter = ResourceExporter()
    aggregated = {"env": {}, "secret_env": {}, "files": {}, "secret_files": {}}

    for resource in resources:
        exported = exporter.export_resource(resource, resource.get("parent_chain", []))

        for kind in ["env", "secret_env", "files", "secret_files"]:
            for entry in exported[kind]:
                key = entry["key"]
                value = entry["value"]

                # secret 类型在不包含敏感值时用占位符
                if kind.startswith("secret") and not include_secrets:
                    value = "***"

                # 后面的资源覆盖前面的（如果 key 重复）
                aggregated[kind][key] = value

    return aggregated
