"""内部缓存失效端点 — 供其他服务（如 lea-config）调用清除缓存。

仅限内部调用，不做鉴权（依赖网络隔离）。
"""

from fastapi import APIRouter
from pydantic import BaseModel

from lea_tools.services.cache import invalidate_capabilities

router = APIRouter(prefix="/internal", tags=["internal"])


class InvalidateCacheRequest(BaseModel):
    tenant_id: str
    cache_type: str = "capabilities"  # 目前只有这一种


@router.post("/invalidate-cache")
async def invalidate_cache(req: InvalidateCacheRequest):
    """清除指定租户的缓存。

    工具 CRUD / MCP 同步后由 lea-config 或本服务自己调用。

    Args:
        tenant_id: 租户 ID
        cache_type: 缓存类型（当前只支持 "capabilities"）

    Returns:
        {"status": "ok"}
    """
    if req.cache_type == "capabilities":
        await invalidate_capabilities(req.tenant_id)

    return {"status": "ok"}
