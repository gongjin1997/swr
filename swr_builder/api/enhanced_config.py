"""配置管理 API 路由"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..access import require_principal
from ..api.auth import Principal
from ..errors import DomainError, Forbidden, NotFound
from ..services import enhanced_config as config_service
from ..store import get_store

router = APIRouter(prefix="/api/configs", tags=["configs"])


# === Request/Response Models ===

class ConfigEntryCreate(BaseModel):
    """创建配置条目请求"""
    scope_type: str = Field(..., description="作用域类型")
    scope_id: int = Field(..., description="作用域 ID")
    key: str = Field(..., description="配置键")
    value: str = Field(..., description="配置值")
    stage: str = Field(default="app", description="配置阶段")
    kind: str = Field(default="common", description="配置类型")
    process_type: str = Field(default="env", description="处理类型")
    locked: bool = Field(default=False, description="是否锁定")
    description: str = Field(default="", description="描述")


class ConfigEntryUpdate(BaseModel):
    """更新配置条目请求"""
    value: str = Field(..., description="配置值")
    kind: Optional[str] = Field(None, description="配置类型")
    process_type: Optional[str] = Field(None, description="处理类型")
    locked: Optional[bool] = Field(None, description="是否锁定")
    description: Optional[str] = Field(None, description="描述")


class ConfigBatchCreate(BaseModel):
    """批量创建配置请求"""
    scope_type: str
    scope_id: int
    configs: List[Dict[str, Any]]


class ConfigTemplateApply(BaseModel):
    """应用配置模板请求"""
    template_id: int
    scope_type: str
    scope_id: int


class ConfigExport(BaseModel):
    """导出配置请求"""
    scope_type: str
    scope_id: int
    format: str = Field(default="json", description="导出格式: json/yaml")


class ConfigImport(BaseModel):
    """导入配置请求"""
    scope_type: str
    scope_id: int
    data: str
    format: str = Field(default="json", description="导入格式: json/yaml")
    overwrite: bool = Field(default=False, description="是否覆盖已存在的配置")


# === API Endpoints ===

@router.post("", summary="创建配置条目")
def create_config(
    req: ConfigEntryCreate,
    me: Principal = Depends(require_principal),
) -> Dict[str, Any]:
    """创建配置条目"""
    try:
        store = get_store()
        return config_service.create_config(
            store,
            me,
            scope_type=req.scope_type,
            scope_id=req.scope_id,
            key=req.key,
            value=req.value,
            stage=req.stage,
            kind=req.kind,
            process_type=req.process_type,
            locked=req.locked,
            description=req.description,
        )
    except Forbidden as e:
        raise HTTPException(status_code=403, detail=str(e))
    except DomainError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{entry_id}", summary="更新配置条目")
def update_config(
    entry_id: int,
    req: ConfigEntryUpdate,
    me: Principal = Depends(require_principal),
) -> Dict[str, Any]:
    """更新配置条目"""
    try:
        store = get_store()
        return config_service.update_config(
            store,
            me,
            entry_id=entry_id,
            value=req.value,
            kind=req.kind,
            process_type=req.process_type,
            locked=req.locked,
            description=req.description,
        )
    except NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Forbidden as e:
        raise HTTPException(status_code=403, detail=str(e))
    except DomainError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{entry_id}", summary="删除配置条目")
def delete_config(
    entry_id: int,
    me: Principal = Depends(require_principal),
) -> Dict[str, str]:
    """删除配置条目"""
    try:
        store = get_store()
        config_service.delete_config(store, me, entry_id)
        return {"message": "配置已删除"}
    except NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Forbidden as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.get("", summary="列出配置条目")
def list_configs(
    scope_type: Optional[str] = Query(None),
    scope_id: Optional[int] = Query(None),
    stage: Optional[str] = Query(None),
    kind: Optional[str] = Query(None),
    process_type: Optional[str] = Query(None),
    me: Principal = Depends(require_principal),
) -> List[Dict[str, Any]]:
    """列出配置条目"""
    try:
        store = get_store()
        return config_service.list_configs(
            store,
            me,
            scope_type=scope_type,
            scope_id=scope_id,
            stage=stage,
            kind=kind,
            process_type=process_type,
        )
    except Forbidden as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.get("/services/{service_id}/resolved", summary="获取服务完整配置")
def get_service_config(
    service_id: int,
    stage: str = Query(default="app"),
    me: Principal = Depends(require_principal),
) -> Dict[str, Any]:
    """获取服务的完整配置（按优先级解析）"""
    try:
        store = get_store()
        return config_service.get_service_config(store, me, service_id, stage)
    except NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Forbidden as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post("/batch", summary="批量创建配置")
def batch_create_configs(
    req: ConfigBatchCreate,
    me: Principal = Depends(require_principal),
) -> List[Dict[str, Any]]:
    """批量创建配置"""
    try:
        store = get_store()
        return config_service.batch_create(
            store,
            me,
            scope_type=req.scope_type,
            scope_id=req.scope_id,
            configs=req.configs,
        )
    except Forbidden as e:
        raise HTTPException(status_code=403, detail=str(e))
    except DomainError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/templates/apply", summary="应用配置模板")
def apply_config_template(
    req: ConfigTemplateApply,
    me: Principal = Depends(require_principal),
) -> List[Dict[str, Any]]:
    """应用配置模板"""
    try:
        store = get_store()
        return config_service.apply_template(
            store,
            me,
            template_id=req.template_id,
            scope_type=req.scope_type,
            scope_id=req.scope_id,
        )
    except Forbidden as e:
        raise HTTPException(status_code=403, detail=str(e))
    except DomainError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/history", summary="获取配置变更历史")
def get_config_history(
    config_entry_id: Optional[int] = Query(None),
    scope_type: Optional[str] = Query(None),
    scope_id: Optional[int] = Query(None),
    key: Optional[str] = Query(None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    me: Principal = Depends(require_principal),
) -> List[Dict[str, Any]]:
    """获取配置变更历史"""
    try:
        store = get_store()
        return config_service.get_config_history(
            store,
            me,
            config_entry_id=config_entry_id,
            scope_type=scope_type,
            scope_id=scope_id,
            key=key,
            limit=limit,
            offset=offset,
        )
    except Forbidden as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.get("/deployments/{deployment_id}/snapshot", summary="获取部署配置快照")
def get_deployment_snapshot(
    deployment_id: int,
    me: Principal = Depends(require_principal),
) -> Dict[str, Any]:
    """获取部署的配置快照"""
    try:
        store = get_store()
        snapshot = config_service.get_deployment_config_snapshot(store, me, deployment_id)
        if not snapshot:
            raise HTTPException(status_code=404, detail="配置快照不存在")
        return snapshot
    except NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Forbidden as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.get("/services/{service_id}/compare", summary="对比配置差异")
def compare_configs(
    service_id: int,
    deployment_id_1: int = Query(..., description="第一个部署 ID"),
    deployment_id_2: int = Query(..., description="第二个部署 ID"),
    me: Principal = Depends(require_principal),
) -> Dict[str, Any]:
    """对比两个部署的配置差异"""
    try:
        store = get_store()
        return config_service.compare_configs(
            store,
            me,
            service_id=service_id,
            deployment_id_1=deployment_id_1,
            deployment_id_2=deployment_id_2,
        )
    except NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Forbidden as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post("/export", summary="导出配置")
def export_configs(
    req: ConfigExport,
    me: Principal = Depends(require_principal),
) -> Dict[str, str]:
    """导出配置（JSON/YAML）"""
    try:
        store = get_store()
        data = config_service.export_configs(
            store,
            me,
            scope_type=req.scope_type,
            scope_id=req.scope_id,
            format=req.format,
        )
        return {"data": data, "format": req.format}
    except Forbidden as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post("/import", summary="导入配置")
def import_configs(
    req: ConfigImport,
    me: Principal = Depends(require_principal),
) -> List[Dict[str, Any]]:
    """导入配置（JSON/YAML）"""
    try:
        store = get_store()
        return config_service.import_configs(
            store,
            me,
            scope_type=req.scope_type,
            scope_id=req.scope_id,
            data=req.data,
            format=req.format,
            overwrite=req.overwrite,
        )
    except Forbidden as e:
        raise HTTPException(status_code=403, detail=str(e))
    except DomainError as e:
        raise HTTPException(status_code=400, detail=str(e))


# === Configuration Templates ===

@router.get("/templates", summary="列出配置模板")
def list_templates(
    category: Optional[str] = Query(None),
    scope_type: Optional[str] = Query(None),
    me: Principal = Depends(require_principal),
) -> List[Dict[str, Any]]:
    """列出配置模板"""
    store = get_store()

    conditions = []
    params = []

    if category:
        conditions.append("category = %s")
        params.append(category)

    if scope_type:
        conditions.append("scope_type = %s")
        params.append(scope_type)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    return store.execute_all(
        f"SELECT * FROM config_templates {where} ORDER BY category, name",
        *params
    )


@router.get("/templates/{template_id}", summary="获取配置模板详情")
def get_template(
    template_id: int,
    me: Principal = Depends(require_principal),
) -> Dict[str, Any]:
    """获取配置模板详情"""
    store = get_store()
    template = store.execute_one(
        "SELECT * FROM config_templates WHERE id = %s",
        template_id
    )

    if not template:
        raise HTTPException(status_code=404, detail="配置模板不存在")

    import json
    template["entries"] = json.loads(template["entries"])

    return template
