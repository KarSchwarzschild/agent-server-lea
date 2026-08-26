"""工具执行接口（含配额限流）。"""

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Any

from lea_tools.db import get_db
from lea_tools.db.models import ToolPermission
from lea_tools.executor.router import ToolCall, execute
from lea_tools.services import check_permission, resolve_tool
from lea_tools.services.credential import get_credential_for_tool
from lea_tools.services.cache import check_rate_limit

router = APIRouter(prefix="/tools", tags=["tools"])


class ExecuteRequest(BaseModel):
    tool_name: str
    params: dict[str, Any] = {}
    session_id: str = ""


class ExecuteResponse(BaseModel):
    status: str
    result: Any = None
    error: str | None = None
    usage: dict = {}


@router.post("/execute", response_model=ExecuteResponse)
async def execute_tool(
    req: ExecuteRequest,
    x_tenant_id: str = Header(..., alias="X-Tenant-ID"),
    x_user_id: str | None = Header(None, alias="X-User-ID"),
    x_user_role: str = Header("user", alias="X-User-Role"),
    x_agent_id: str | None = Header(None, alias="X-Agent-ID"),
    db: Session = Depends(get_db),
):
    """执行一个工具。

    ⚠️ `X-User-ID` 是**可选**的，不是必填 —— 缺了不返 422，而是退化成"只能用平台
    工具"（见 visible_tools 的 fail-closed 说明）。必填的话任何还没跟上的调用方
    会从"少几个工具"变成"整条工具链 422"，那是个比漏工具严重得多的失败。
    """
    agent_id = int(x_agent_id) if x_agent_id else None

    if not check_permission(
        db, x_tenant_id, x_user_id, x_user_role, agent_id, req.tool_name
    ):
        raise HTTPException(403, "tool not allowed")

    # 走 resolve_tool（平台的 + 这个人自己的，同名时收敛并告警）。以前是裸的
    # Tool.name == ...，于是全库唯一的那一行会被任何租户解析到 —— 而工具行里有
    # endpoint 和适配器配置。
    tool = resolve_tool(db, x_tenant_id, x_user_id, req.tool_name)
    if not tool:
        raise HTTPException(404, f"tool '{req.tool_name}' not found")

    rate_rule = (
        db.query(ToolPermission)
        .filter(
            ToolPermission.tool_id == tool.id,
            ToolPermission.tenant_id == x_tenant_id,
            ToolPermission.rate_limit > 0,
        )
        .first()
    )
    if rate_rule:
        allowed = await check_rate_limit(x_tenant_id, req.tool_name, rate_rule.rate_limit)
        if not allowed:
            raise HTTPException(429, "rate limit exceeded")

    credential = get_credential_for_tool(db, x_tenant_id, tool.id)

    call = ToolCall(
        tool_name=req.tool_name,
        params=req.params,
        session_id=req.session_id,
        execution_type=tool.execution_type,
        endpoint=tool.endpoint,
        http_adapter_config=tool.http_adapter_config,
        mcp_server_id=tool.mcp_server_id,
        sandbox_config=tool.sandbox_config,
    )

    result = await execute(call, credential)
    return ExecuteResponse(
        status=result.status, result=result.output, error=result.error, usage=result.usage
    )
