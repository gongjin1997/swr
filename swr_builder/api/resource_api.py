"""资源管理 API（基于新的领域模型）。

提供资源和资源绑定的 REST 接口。
"""
from typing import Any, Dict

from fastapi import APIRouter, Query, Response

from ..access.routes import authenticated
from ..services import resource_service
from ..store import IntegrityViolation, Store
from . import dep

router = APIRouter(prefix="/api/resources", tags=["resources"])
service_bindings_router = APIRouter(prefix="/api/services", tags=["service-bindings"])


# ============================================================================
# 资源类型
# ============================================================================

@router.get("/types")
@authenticated
async def list_resource_types(me=dep.principal) -> Dict[str, Any]:
    """列出所有资源类型定义"""
    return resource_service.list_resource_types()


# ============================================================================
# 资源管理
# ============================================================================

@router.get("")
@authenticated
async def list_resources(
    me=dep.principal,
    store: Store = dep.store,
    scope_type: str = Query(None),
    scope_id: int = Query(None),
    resource_type: str = Query(None),
    limit: int = Query(100, le=100),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """列出资源"""
    try:
        return resource_service.list_resources(store, me, scope_type, scope_id, resource_type, limit, offset)
    except PermissionError as e:
        return Response(status_code=403, content=str(e))


@router.post("")
@authenticated
async def create_resource(
    data: Dict[str, Any],
    me=dep.principal,
    store: Store = dep.store,
    box=dep.box,
    audit=dep.audit,
) -> Dict[str, Any]:
    """创建资源"""
    try:
        resource = resource_service.create_resource(store, box, me, data)
        audit(f"创建资源 {resource['name']}", resource_id=resource["id"])
        return resource
    except IntegrityViolation as e:
        if "uq_resources_scope_name" in str(e):
            return Response(status_code=409, content="资源名称已存在")
        raise
    except (ValueError, PermissionError) as e:
        return Response(status_code=400 if isinstance(e, ValueError) else 403, content=str(e))


@router.get("/{resource_id}")
@authenticated
async def get_resource(
    resource_id: int,
    me=dep.principal,
    store: Store = dep.store,
) -> Dict[str, Any]:
    """获取资源详情"""
    try:
        return resource_service.get_resource(store, me, resource_id)
    except (ValueError, PermissionError) as e:
        return Response(status_code=404 if "不存在" in str(e) else 403, content=str(e))


@router.put("/{resource_id}")
@authenticated
async def update_resource(
    resource_id: int,
    data: Dict[str, Any],
    me=dep.principal,
    store: Store = dep.store,
    box=dep.box,
    audit=dep.audit,
) -> Dict[str, Any]:
    """更新资源属性（创建新版本）"""
    try:
        resource = resource_service.update_resource(store, box, me, resource_id, data)
        audit(f"更新资源 {resource['name']}", resource_id=resource_id)
        return resource
    except (ValueError, PermissionError) as e:
        return Response(status_code=404 if "不存在" in str(e) else 403, content=str(e))


@router.delete("/{resource_id}")
@authenticated
async def delete_resource(
    resource_id: int,
    me=dep.principal,
    store: Store = dep.store,
    audit=dep.audit,
) -> Response:
    """删除资源"""
    try:
        resource = store.get_resource(resource_id)
        if resource:
            name = resource["name"]
            resource_service.delete_resource(store, me, resource_id)
            audit(f"删除资源 {name}", resource_id=resource_id)
        return Response(status_code=204)
    except (ValueError, PermissionError) as e:
        return Response(status_code=404 if "不存在" in str(e) else 403, content=str(e))


# ============================================================================
# 服务资源绑定
# ============================================================================

@service_bindings_router.get("/{service_id}/resource-bindings")
@authenticated
async def list_service_bindings(
    service_id: int,
    me=dep.principal,
    store: Store = dep.store,
) -> Dict[str, Any]:
    """列出服务的资源绑定"""
    try:
        items = resource_service.list_service_bindings(store, me, service_id)
        return {"items": items, "total": len(items)}
    except (ValueError, PermissionError) as e:
        return Response(status_code=404 if "不存在" in str(e) else 403, content=str(e))


@service_bindings_router.post("/{service_id}/resource-bindings")
@authenticated
async def bind_resource(
    service_id: int,
    data: Dict[str, Any],
    me=dep.principal,
    store: Store = dep.store,
    audit=dep.audit,
) -> Dict[str, Any]:
    """绑定资源到服务"""
    resource_id = data.get("resource_id")
    alias = data.get("alias", "")

    if not resource_id:
        return Response(status_code=400, content="缺少 resource_id")

    try:
        binding = resource_service.bind_resource_to_service(store, me, service_id, resource_id, alias)
        audit(f"绑定资源到服务", service_id=service_id, resource_id=resource_id)
        return binding
    except IntegrityViolation as e:
        if "service_resource_bindings" in str(e):
            return Response(status_code=409, content="资源已绑定")
        raise
    except (ValueError, PermissionError) as e:
        return Response(status_code=404 if "不存在" in str(e) else 403, content=str(e))


@service_bindings_router.delete("/{service_id}/resource-bindings/{binding_id}")
@authenticated
async def unbind_resource(
    service_id: int,
    binding_id: int,
    me=dep.principal,
    store: Store = dep.store,
    audit=dep.audit,
) -> Response:
    """解绑资源"""
    try:
        resource_service.unbind_resource_from_service(store, me, binding_id)
        audit(f"解绑资源", service_id=service_id, binding_id=binding_id)
        return Response(status_code=204)
    except (ValueError, PermissionError) as e:
        return Response(status_code=404 if "不存在" in str(e) else 403, content=str(e))


@service_bindings_router.get("/{service_id}/resource-exports")
@authenticated
async def export_service_resources(
    service_id: int,
    me=dep.principal,
    store: Store = dep.store,
    box=dep.box,
    include_secrets: bool = Query(False),
) -> Dict[str, Any]:
    """导出服务绑定的资源为配置"""
    try:
        return resource_service.export_service_resources(store, box, me, service_id, include_secrets)
    except (ValueError, PermissionError) as e:
        return Response(status_code=404 if "不存在" in str(e) else 403, content=str(e))
