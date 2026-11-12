"""环境预置 API。

GET    /api/presets              列出预置
POST   /api/presets              创建预置
GET    /api/presets/:id          获取预置详情
PUT    /api/presets/:id          更新预置
DELETE /api/presets/:id          删除预置
POST   /api/presets/:id/apply    应用预置到环境
"""
from typing import Any, Dict

from fastapi import APIRouter, Query, Response

from ..access.routes import authenticated
from ..services import presets as preset_service
from ..store import IntegrityViolation, Store
from . import dep

router = APIRouter(prefix="/api/presets", tags=["presets"])


@router.get("")
@authenticated
async def list_presets(
    me=dep.principal,
    store: Store = dep.store,
    limit: int = Query(100, le=100),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """列出预置"""
    return preset_service.list_presets(store, me, limit, offset)


@router.post("")
@authenticated
async def create_preset(data: Dict[str, Any], me=dep.principal, store: Store = dep.store,
                        audit=dep.audit) -> Dict[str, Any]:
    """创建预置"""
    try:
        preset = preset_service.create_preset(store, me, data)
        audit(f"创建环境预置 {preset['name']}", preset_id=preset["id"])
        return preset
    except IntegrityViolation as e:
        if "uq_environment_presets_name" in str(e):
            return Response(status_code=409, content="预置名称已存在")
        raise
    except (ValueError, PermissionError) as e:
        return Response(status_code=400 if isinstance(e, ValueError) else 403, content=str(e))


@router.get("/{preset_id}")
@authenticated
async def get_preset(preset_id: int, me=dep.principal, store: Store = dep.store) -> Dict[str, Any]:
    """获取预置详情"""
    try:
        return preset_service.get_preset(store, me, preset_id)
    except (ValueError, PermissionError) as e:
        return Response(status_code=404 if "不存在" in str(e) else 403, content=str(e))


@router.put("/{preset_id}")
@authenticated
async def update_preset(preset_id: int, data: Dict[str, Any], me=dep.principal,
                        store: Store = dep.store, audit=dep.audit) -> Dict[str, Any]:
    """更新预置"""
    try:
        preset = preset_service.update_preset(store, me, preset_id, data)
        audit(f"更新环境预置 {preset['name']}", preset_id=preset_id)
        return preset
    except IntegrityViolation as e:
        if "uq_environment_presets_name" in str(e):
            return Response(status_code=409, content="预置名称已存在")
        raise
    except (ValueError, PermissionError) as e:
        return Response(status_code=404 if "不存在" in str(e) else 403, content=str(e))


@router.delete("/{preset_id}")
@authenticated
async def delete_preset(preset_id: int, me=dep.principal, store: Store = dep.store,
                        audit=dep.audit) -> Response:
    """删除预置"""
    try:
        preset = store.get_preset(preset_id)
        if preset:
            name = preset["name"]
            preset_service.delete_preset(store, me, preset_id)
            audit(f"删除环境预置 {name}", preset_id=preset_id)
        return Response(status_code=204)
    except (ValueError, PermissionError) as e:
        return Response(status_code=404 if "不存在" in str(e) else 403, content=str(e))


@router.post("/{preset_id}/apply")
@authenticated
async def apply_preset(preset_id: int, data: Dict[str, Any], me=dep.principal,
                       store: Store = dep.store, box=dep.box, audit=dep.audit) -> Dict[str, Any]:
    """应用预置到环境"""
    namespace_id = data.get("namespace_id")
    if not namespace_id:
        return Response(status_code=400, content="缺少 namespace_id")

    try:
        result = preset_service.apply_preset(store, box, me, namespace_id, preset_id)
        audit(f"应用预置 {preset_id} 到环境 {namespace_id}", preset_id=preset_id, namespace_id=namespace_id)
        return result
    except (ValueError, PermissionError) as e:
        return Response(status_code=404 if "不存在" in str(e) else 403, content=str(e))
