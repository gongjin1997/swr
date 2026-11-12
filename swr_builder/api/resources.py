"""资源管理 API。

GET    /api/resources              列出资源
POST   /api/resources              创建资源
GET    /api/resources/:id          获取资源详情
PUT    /api/resources/:id          更新资源
DELETE /api/resources/:id          删除资源
GET    /api/resources/:id/versions 列出资源版本
GET    /api/resources/:id/versions/:version 获取指定版本
GET    /api/resources/:id/usage    查看资源使用情况

GET    /api/services/:id/bindings  列出服务绑定的资源
POST   /api/services/:id/bindings  绑定资源
DELETE /api/services/:id/bindings/:resource_id 解除绑定
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query, Response

from ..access.routes import authenticated, requires
from ..delivery.resources import RESOURCE_TYPES
from ..services import resources as resource_service
from ..store import IntegrityViolation, Store
from . import dep

router = APIRouter(prefix="/api/resources", tags=["resources"])
service_bindings_router = APIRouter(prefix="/api/services", tags=["service-bindings"])


@router.get("")
@authenticated
async def list_resources(
    me=dep.principal,
    store: Store = dep.store,
    scope_type: Optional[str] = Query(None),
    scope_id: Optional[int] = Query(None),
    type: Optional[str] = Query(None),
    limit: int = Query(100, le=100),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """列出资源"""
    return resource_service.list_resources(store, me, scope_type=scope_type, scope_id=scope_id,
                                           type_name=type, limit=limit, offset=offset)


@router.get("/types")
@authenticated
async def list_resource_types() -> Dict[str, Any]:
    """列出资源类型定义"""
    types = []
    for type_name, spec in RESOURCE_TYPES.items():
        types.append({
            "type": type_name,
            "label": spec["label"],
            "parent_type": spec["parent_type"],
            "attributes": [
                {
                    "name": name,
                    "label": attr_spec.get("label", name),
                    "type": attr_spec["type"],
                    "secret": attr_spec["secret"],
                    "required": attr_spec["required"],
                    "default": attr_spec.get("default"),
                }
                for name, attr_spec in spec["attributes"].items()
            ],
        })
    return {"types": types}


@router.post("")
@authenticated
async def create_resource(data: Dict[str, Any], me=dep.principal, store: Store = dep.store,
                          box=dep.box, audit=dep.audit) -> Dict[str, Any]:
    """创建资源"""
    try:
        resource = resource_service.create_resource(store, box, me, data)
        audit(f"创建资源 {resource['name']} ({resource['type']})", resource_id=resource["id"])
        return resource
    except IntegrityViolation as e:
        if "uq_resources_scope_name" in str(e):
            return Response(status_code=409, content="该作用域下已存在同名资源")
        raise
    except (ValueError, PermissionError) as e:
        return Response(status_code=400, content=str(e))


@router.get("/{resource_id}")
@authenticated
async def get_resource(resource_id: int, me=dep.principal, store: Store = dep.store,
                       reveal: bool = Query(False)) -> Dict[str, Any]:
    """获取资源详情"""
    try:
        return resource_service.get_resource(store, me, resource_id, reveal=reveal)
    except (ValueError, PermissionError) as e:
        return Response(status_code=404 if "不存在" in str(e) else 403, content=str(e))


@router.put("/{resource_id}")
@authenticated
async def update_resource(resource_id: int, data: Dict[str, Any], me=dep.principal,
                          store: Store = dep.store, box=dep.box, audit=dep.audit) -> Dict[str, Any]:
    """更新资源"""
    try:
        resource = resource_service.update_resource(store, box, me, resource_id, data)
        audit(f"更新资源 {resource['name']}", resource_id=resource_id)
        return resource
    except (ValueError, PermissionError) as e:
        return Response(status_code=404 if "不存在" in str(e) else 403, content=str(e))


@router.delete("/{resource_id}")
@authenticated
async def delete_resource(resource_id: int, me=dep.principal, store: Store = dep.store,
                          audit=dep.audit) -> Response:
    """删除资源"""
    try:
        resource = store.get_resource(resource_id)
        if resource:
            name = resource["name"]
            resource_service.delete_resource(store, me, resource_id)
            audit(f"删除资源 {name}", resource_id=resource_id)
        return Response(status_code=204)
    except ValueError as e:
        if "被" in str(e) and "使用" in str(e):
            return Response(status_code=409, content=str(e))
        return Response(status_code=404, content=str(e))
    except PermissionError as e:
        return Response(status_code=403, content=str(e))
    except IntegrityViolation as e:
        if "fk" in str(e).lower() or "restrict" in str(e).lower():
            return Response(status_code=409, content="资源被服务绑定，不能删除")
        raise


@router.get("/{resource_id}/versions")
@authenticated
async def list_resource_versions(resource_id: int, me=dep.principal, store: Store = dep.store) -> Dict[str, Any]:
    """列出资源版本"""
    try:
        versions = resource_service.list_resource_versions(store, me, resource_id)
        return {"versions": versions}
    except (ValueError, PermissionError) as e:
        return Response(status_code=404 if "不存在" in str(e) else 403, content=str(e))


@router.get("/{resource_id}/versions/{version_number}")
@authenticated
async def get_resource_version(resource_id: int, version_number: int, me=dep.principal,
                                store: Store = dep.store, box=dep.box, reveal: bool = Query(False)) -> Dict[str, Any]:
    """获取指定版本"""
    try:
        return resource_service.get_resource_version(store, box, me, resource_id, version_number, reveal=reveal)
    except (ValueError, PermissionError) as e:
        return Response(status_code=404 if "不存在" in str(e) else 403, content=str(e))


@router.get("/{resource_id}/usage")
@authenticated
async def resource_usage(resource_id: int, me=dep.principal, store: Store = dep.store) -> Dict[str, Any]:
    """查看资源使用情况"""
    try:
        usage = resource_service.resource_usage(store, me, resource_id)
        return {"usage": usage}
    except (ValueError, PermissionError) as e:
        return Response(status_code=404 if "不存在" in str(e) else 403, content=str(e))


# === 服务资源绑定 ===

@service_bindings_router.get("/{service_id}/bindings")
@authenticated
@requires("service.config")
async def list_service_bindings(service_id: int, me=dep.principal, store: Store = dep.store) -> Dict[str, Any]:
    """列出服务绑定的资源"""
    bindings = store.list_bindings(service_id)
    return {"bindings": bindings}


@service_bindings_router.post("/{service_id}/bindings")
@authenticated
@requires("service.config")
async def bind_resource(service_id: int, data: Dict[str, Any], me=dep.principal,
                        store: Store = dep.store, audit=dep.audit) -> Dict[str, Any]:
    """绑定资源到服务"""
    resource_id = data.get("resource_id")
    alias = data.get("alias", "")
    if not resource_id:
        return Response(status_code=400, content="缺少 resource_id")

    try:
        binding = store.bind_resource(service_id, resource_id, alias, created_by=me.user_id)
        audit(f"服务 {service_id} 绑定资源 {resource_id}", service_id=service_id, resource_id=resource_id)
        return binding
    except IntegrityViolation as e:
        if "fk" in str(e).lower():
            return Response(status_code=404, content="服务或资源不存在")
        raise


@service_bindings_router.delete("/{service_id}/bindings/{resource_id}")
@authenticated
@requires("service.config")
async def unbind_resource(service_id: int, resource_id: int, me=dep.principal,
                          store: Store = dep.store, audit=dep.audit) -> Response:
    """解除绑定"""
    if store.unbind_resource(service_id, resource_id):
        audit(f"服务 {service_id} 解除资源绑定 {resource_id}", service_id=service_id, resource_id=resource_id)
    return Response(status_code=204)
