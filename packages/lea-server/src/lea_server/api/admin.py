"""管理API — App列表、系统信息。"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Request

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/apps")
async def list_apps(request: Request):
    """返回所有已注册App的完整信息。"""
    registry = request.app.state.registry
    result = []
    for name, manifest in registry.manifests.items():
        result.append(asdict(manifest))
    return {"apps": result}


@router.get("/apps/{app_name}")
async def get_app(app_name: str, request: Request):
    """返回单个App的详细信息。"""
    registry = request.app.state.registry
    manifest = registry.manifests.get(app_name)
    if manifest is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"App not found: {app_name}")
    return asdict(manifest)
