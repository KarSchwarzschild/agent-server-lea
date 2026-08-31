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
        # ⚠️ 分键用的是**暴露名**，而配额规则是按 tool_id 查出来的 —— 同一行工具在
        # `my__weather` 和 `weather` 两个名字下会各占一个桶，配额实际翻倍。
        #
        # 今天影响很小（只有用户私有工具带前缀，而它们的规则一般是给自己配的），而且
        # 方向是偏松不是功能失败，所以不在这一轮改：改它要想清楚"配额是按行还是按名"，
        # 而那关系到租户共享工具的计费口径。留着这行注释，别让它变成一个没人知道的事实。
        allowed = await check_rate_limit(x_tenant_id, req.tool_name, rate_rule.rate_limit)
        if not allowed:
            raise HTTPException(429, "rate limit exceeded")

    credential = get_credential_for_tool(db, x_tenant_id, tool.id)

    call = ToolCall(
        # ⚠️ 用**解析出来那一行的 name**（库里的裸名），不是 req.tool_name（模型发来的
        # 暴露名，用户私有工具带 my__ 前缀）。
        #
        # 这一跳错了会静默失败，而且只在**用户私有的 MCP 工具**上显形：
        #
        #   模型发    my__mcp_30_lookup_ticket
        #   库里是    mcp_30_lookup_ticket
        #   远端要    lookup_ticket
        #
        # remote_tool_name 只剥 mcp_{id}_ 前缀，碰到 my__mcp_30_... 时 startswith 为假，
        # 于是**原样发给远端** —— 远端回 unknown tool，模型说"工具不可用"。请求全程 200。
        #
        # 真链路验收（scripts/session_attach_mcp_e2e.py）抓到的：假上游的访问日志里记着
        # 一次对 `my__mcp_30_lookup_ticket` 的调用。单测抓不到 —— 它把 call_tool 整个
        # mock 掉了，正好跳过这一跳。
        #
        # 为什么不是去改 remote_tool_name 让它多剥一层：那会把"归属命名空间"这个概念
        # 泄进 MCP 代理里。剥 my__ 是 resolve_tool 的职责，而它已经做了 —— 这里只要
        # 别把它的成果丢掉。
        tool_name=tool.name,
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
