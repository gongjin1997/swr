"""环境预置 API 路由。"""
from __future__ import annotations

from fastapi import APIRouter, Body, Request

from ..web.deps import Ctx, Me, audit, authenticated, requires
from ..services import presets

router = APIRouter()


@router.get("/api/presets")
@authenticated
async def list_presets(ctx: Ctx, me: Me):
    """列出环境预置"""
    items = presets.list_presets(ctx.store, me)
    return {"items": items, "total": len(items)}


@router.post("/api/presets", status_code=201)
@requires("config.manage")
async def create_preset(request: Request, ctx: Ctx, me: Me, data: dict = Body(...)):
    """创建环境预置"""
    preset = presets.create_preset(ctx.store, me, data)
    audit(request, "preset.create", preset["id"], f"name={preset['name']}")
    return preset


@router.get("/api/presets/{id}")
@authenticated
async def get_preset(id: int, ctx: Ctx, me: Me):
    """获取环境预置详情"""
    return presets.get_preset(ctx.store, me, id)


@router.put("/api/presets/{id}")
@requires("config.manage")
async def update_preset(id: int, request: Request, ctx: Ctx, me: Me, data: dict = Body(...)):
    """更新环境预置"""
    preset = presets.update_preset(ctx.store, me, id, data)
    audit(request, "preset.update", id, f"name={preset['name']}")
    return preset


@router.delete("/api/presets/{id}", status_code=204)
@requires("config.manage")
async def delete_preset(id: int, request: Request, ctx: Ctx, me: Me):
    """删除环境预置"""
    presets.delete_preset(ctx.store, me, id)
    audit(request, "preset.delete", id, "")


@router.post("/api/environments/{id}/apply-preset", status_code=200)
@authenticated
async def apply_preset(id: int, request: Request, ctx: Ctx, me: Me, data: dict = Body(...)):
    """应用预置到环境"""
    preset_id = data.get("preset_id")
    if not isinstance(preset_id, int):
        raise ValueError("preset_id 必须是整数")

    environment = presets.apply_preset(ctx.store, ctx.box, me, id, preset_id)
    audit(request, "environment.apply_preset", id, f"preset_id={preset_id}")
    return environment
