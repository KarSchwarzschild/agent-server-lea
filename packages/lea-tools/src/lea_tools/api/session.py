"""Session capabilities 接口（async + cached）。"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from lea_tools.db import get_db
from lea_tools.services.cache import get_cached_capabilities, set_cached_capabilities
from lea_tools.services.registry import list_tools_for_session

router = APIRouter(prefix="/session", tags=["session"])


class CapabilityRequest(BaseModel):
    tenant_id: str
    user_id: str
    role: str = "user"
    agent_id: int | None = None


class CapabilityResponse(BaseModel):
    tools: list[dict]
    ttl: int = 300
    cached: bool = False


@router.post("/capabilities", response_model=CapabilityResponse)
async def get_capabilities(req: CapabilityRequest, db: Session = Depends(get_db)):
    """这个 session 能用的工具清单。

    `req.user_id` 以前**收到了但丢掉了** —— 请求体里一直有这个字段，下面两处却都不
    传。工具按租户可见时那没有可观察的后果，改成按人可见之后它就是"把 alice 的清单
    发给 bob"。
    """
    cached = await get_cached_capabilities(
        req.tenant_id, req.user_id, req.role, req.agent_id
    )
    if cached is not None:
        return CapabilityResponse(tools=cached, cached=True)

    tools = list_tools_for_session(
        db, req.tenant_id, req.user_id, req.role, req.agent_id
    )
    await set_cached_capabilities(
        req.tenant_id, req.user_id, req.role, req.agent_id, tools
    )
    return CapabilityResponse(tools=tools)
