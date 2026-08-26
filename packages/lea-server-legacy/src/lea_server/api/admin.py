"""管理API — App列表、系统信息、基础设施监控。"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Request

from lea_server.core.deps import admin_required
from lea_server.models.models import User

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/apps")
async def list_apps(request: Request, _: User = Depends(admin_required)):
    """返回所有已注册App的完整信息。"""
    registry = request.app.state.registry
    result = []
    for name, manifest in registry.manifests.items():
        result.append(asdict(manifest))
    return {"apps": result}


@router.get("/apps/{app_name}")
async def get_app(app_name: str, request: Request, _: User = Depends(admin_required)):
    """返回单个App的详细信息。"""
    registry = request.app.state.registry
    manifest = registry.manifests.get(app_name)
    if manifest is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"App not found: {app_name}")
    return asdict(manifest)


@router.get("/breakers")
async def get_breakers(_: User = Depends(admin_required)):
    """返回所有熔断器状态。"""
    from lea_graph.infra.resilience import get_breaker_status

    return await get_breaker_status()


@router.get("/usage/{tenant_id}")
async def get_usage(tenant_id: str, request: Request, _: User = Depends(admin_required)):
    """返回指定租户的 Token 配额使用情况。"""
    rate_limiter = request.app.state.rate_limiter
    usage = await rate_limiter.get_usage(tenant_id)
    rpm = await rate_limiter.get_rpm_status(tenant_id)
    return {
        **usage,
        "rpm_remaining": rpm.remaining,
        "rpm_limit": rpm.limit,
    }
