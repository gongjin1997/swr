"""服务配置接口扩展：使用 ParamResolver 提供来源追踪。"""
from typing import Any, Dict

from ..access.principal import Principal
from ..core.adapter import resolve_with_param_resolver
from ..store import Store


def effective_config_with_sources(store: Store, me: Principal, service_id: int) -> Dict[str, Any]:
    """
    获取服务的生效配置，包含完整的来源追踪。

    返回格式：
    {
        "params": {key: value},
        "sources": {
            key: {
                "value": value,
                "source_layer": "resource|config|du_attr",
                "source_type": "resource|config|attribute",
                "source_name": "redis-prod",
                "scope": "platform|environment|du",
                "version": 1,
                "locked": false,
                "overridden": false
            }
        },
        "warnings": [...],
        "errors": [...]
    }
    """
    service = store.get_service(service_id)
    if not service:
        raise ValueError("服务不存在")

    # 检查权限
    if not me.can_namespace(service["namespace_id"], "service.config"):
        raise PermissionError("无权查看服务配置")

    # 获取服务规格
    if service.get("current_revision_id"):
        revision = store.get_service_revision(service_id, service["current_revision_id"])
        spec = revision["spec"] if revision else {}
    else:
        spec = {}

    # 使用 ParamResolver 解析
    resolved = resolve_with_param_resolver(
        store=store,
        service=service,
        spec=spec,
        artifact=None,
        external_params=None,
        required_params=None,
    )

    # 转换为 API 响应格式
    return {
        "params": resolved.params,
        "sources": {
            key: {
                "value": source.value,
                "source_layer": source.source_layer,
                "source_type": source.source_type,
                "source_id": source.source_id,
                "source_name": source.source_name,
                "scope": source.scope,
                "version": source.version,
                "locked": source.locked,
                "overridden": source.overridden,
                "overridden_by": source.overridden_by,
            }
            for key, source in resolved.sources.items()
        },
        "warnings": resolved.warnings,
        "errors": resolved.errors,
    }
