"""MCP Server CRUD 路由 — platform 级 MCP 配置。

统一 /mcp-servers 路径，Auth 控制权限。

## 为什么 CRUD 在 lea-config 而执行在 lea-tools

分工不是按资源切的，是按职责切的 —— `tools` 表也是这个形状（CRUD 在这里，
`/tools/execute` 在 lea-tools）。lea-tools 不拥有这两张表，它只读。

直接原因见 `lea_tools/api/admin_mcp.py` 的文件头：那边以前有四组 `/admin/*`，四组
都没有授权，`tenant_id` 从请求体取 —— 等于调用方自选租户。而 `require_actor` /
`scoped_filter` / `require_admin` 都在 lea-config 这一侧；lea-tools 的调用方是引擎
不是浏览器，它**没有 actor 概念**，放开用户私有 MCP 之后判断不了「这是不是你的」。

还留在 lea-tools 的只有 `/sync` —— 它是唯一一个写配置数据的例外，P1 搬过来。

## 一个 server 不是一个工具

这里管的是**连接**。工具是 `/sync` 连上去问 `tools/list` 之后生成的 `tools` 行
（`execution_type='mcp'`，`mcp_server_id` 指回来）。所以删一个 server 必须连带删
那些工具行 —— 见下面的 delete。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from lea_authkit import Actor, require_actor
from lea_config.api._auth_helpers import require_admin
from lea_config.database import get_db
from lea_config.models.mcp_server import (
    MCP_SCOPE_PLATFORM,
    MCP_STATUS_UNKNOWN,
    MCP_TRANSPORT_STREAMABLE_HTTP,
    MCP_TRANSPORT_VALUES,
    MCPServer,
)
from lea_config.models.tool import Tool

router = APIRouter(prefix="/mcp-servers", tags=["mcp-servers"])


class MCPServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    # transport 有唯一合法值，所以给默认值 —— 调用方不该被迫填一个只有一个选项的字段。
    # 仍然保留这个字段是为了让「以后会有别的传输方式」这件事在契约上可见。
    transport: str = Field(default=MCP_TRANSPORT_STREAMABLE_HTTP)
    # endpoint 必填。这是这轮修的核心：以前它压根不在创建契约里，于是**每一行建出来
    # 的 endpoint 都是 NULL**，而执行侧读的就是它 —— sync 和调用两条路一起断。
    endpoint: str = Field(min_length=1, max_length=500)
    auth_config: Optional[dict] = None
    tool_filter: Optional[list[str]] = None
    enabled: bool = True


class MCPServerUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    transport: Optional[str] = None
    endpoint: Optional[str] = Field(None, min_length=1, max_length=500)
    auth_config: Optional[dict] = None
    tool_filter: Optional[list[str]] = None
    enabled: Optional[bool] = None


class MCPServerEnable(BaseModel):
    enabled: bool


def _to_dict(s: MCPServer) -> dict:
    """行 → 响应。

    ⚠️ **auth_config 不出现在响应里**，只给一个布尔。它装的是发给远端的 bearer token
    或 api key；列表接口对整个租户可见，把 key 放进去等于任何能列 server 的人都能拿到
    它。写得进去、读不出来，跟 tool_credentials 的取向一致。
    """
    return {
        "id": s.id,
        "tenant_id": s.tenant_id,
        "name": s.name,
        "description": s.description or "",
        "owner_scope": s.owner_scope,
        "transport": s.transport,
        "endpoint": s.endpoint or "",
        "has_auth": bool(s.auth_config),
        "tool_filter": s.tool_filter or [],
        "enabled": s.enabled,
        "connection_status": s.connection_status,
        "last_connected_at": s.last_connected_at.isoformat() if s.last_connected_at else None,
        "last_sync_at": s.last_sync_at.isoformat() if s.last_sync_at else None,
        "last_error": s.last_error or "",
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


def _validate_transport(t: str) -> None:
    if t not in MCP_TRANSPORT_VALUES:
        raise HTTPException(400, f"transport must be one of {MCP_TRANSPORT_VALUES}")


def _validate_endpoint(url: str) -> None:
    """只收 http/https 的绝对 URL。

    这道校验必须在这里，不能只靠 httpx 在调用时报错：这一列的值会被**服务端**拿去
    发请求，所以它是一个 SSRF 面。今天只挡协议（file:// 能读本地文件，其余 scheme
    httpx 直接抛，表现为一个看不懂的 500）。

    ⚠️ 没挡内网地址。真正的 SSRF 防护要解析 DNS 再查网段，而且得防 TOCTOU 和
    重定向 —— 那是一件独立的事，属于「放开用户自己填 URL」（P2）的前置，不是这轮
    的范围。今天能建 server 的只有管理员，风险面是已知的。
    """
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "endpoint must be an absolute http(s) URL")


@router.get("")
def list_servers(
    transport: Optional[str] = Query(None),
    enabled: Optional[bool] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    q = db.query(MCPServer).filter(
        MCPServer.tenant_id == actor.tenant_id,
        MCPServer.owner_scope == MCP_SCOPE_PLATFORM,
    )
    if transport:
        q = q.filter(MCPServer.transport == transport)
    if enabled is not None:
        q = q.filter(MCPServer.enabled == enabled)
    if search:
        like = f"%{search}%"
        q = q.filter((MCPServer.name.ilike(like)) | (MCPServer.description.ilike(like)))
    total = q.count()
    rows = q.order_by(MCPServer.id).offset((page - 1) * size).limit(size).all()
    return {"total": total, "page": page, "size": size, "items": [_to_dict(s) for s in rows]}


@router.get("/{mcp_id}")
def get_server(
    mcp_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    s = db.query(MCPServer).get(mcp_id)
    if not s or s.owner_scope != MCP_SCOPE_PLATFORM:
        raise HTTPException(404, "MCP server not found")
    if not actor.is_platform_admin and s.tenant_id != actor.tenant_id:
        raise HTTPException(404, "MCP server not found")
    return _to_dict(s)


@router.post("", status_code=201)
def create_server(
    body: MCPServerCreate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    _validate_transport(body.transport)
    _validate_endpoint(body.endpoint)
    existing = (
        db.query(MCPServer)
        .filter(
            MCPServer.tenant_id == actor.tenant_id,
            MCPServer.owner_scope == MCP_SCOPE_PLATFORM,
            MCPServer.owner_user_id.is_(None),
            MCPServer.name == body.name,
        )
        .first()
    )
    if existing:
        raise HTTPException(409, f"MCP server '{body.name}' already exists")
    s = MCPServer(
        tenant_id=actor.tenant_id,
        owner_scope=MCP_SCOPE_PLATFORM,
        owner_user_id=None,
        **body.model_dump(),
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return _to_dict(s)


@router.put("/{mcp_id}")
def update_server(
    mcp_id: int,
    body: MCPServerUpdate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    s = db.query(MCPServer).get(mcp_id)
    if not s or s.owner_scope != MCP_SCOPE_PLATFORM:
        raise HTTPException(404, "MCP server not found")
    if not actor.is_platform_admin and s.tenant_id != actor.tenant_id:
        raise HTTPException(404, "MCP server not found")
    data = body.model_dump(exclude_unset=True)
    if "transport" in data:
        _validate_transport(data["transport"])
    if "endpoint" in data:
        _validate_endpoint(data["endpoint"])
        # 换了地址就等于换了一个服务：之前那次连通结果不再代表任何东西。
        # 不清的话「connected」会一直挂在一个已经指向别处的行上。
        s.connection_status = MCP_STATUS_UNKNOWN
        s.last_connected_at = None
        s.last_error = None
    for k, v in data.items():
        setattr(s, k, v)
    db.commit()
    db.refresh(s)
    return _to_dict(s)


@router.patch("/{mcp_id}/enable")
def toggle_enable(
    mcp_id: int,
    body: MCPServerEnable,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    s = db.query(MCPServer).get(mcp_id)
    if not s or s.owner_scope != MCP_SCOPE_PLATFORM:
        raise HTTPException(404, "MCP server not found")
    if not actor.is_platform_admin and s.tenant_id != actor.tenant_id:
        raise HTTPException(404, "MCP server not found")
    s.enabled = body.enabled
    db.commit()
    db.refresh(s)
    return _to_dict(s)


@router.delete("/{mcp_id}", status_code=204)
def delete_server(
    mcp_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    s = db.query(MCPServer).get(mcp_id)
    if not s or s.owner_scope != MCP_SCOPE_PLATFORM:
        raise HTTPException(404, "MCP server not found")
    if not actor.is_platform_admin and s.tenant_id != actor.tenant_id:
        raise HTTPException(404, "MCP server not found")

    # 连带删掉从这个 server 同步进来的工具行。
    #
    # 不删的话留下一批 mcp_server_id 指向已经不存在的 server 的工具：它们仍然
    # enabled、仍然出现在工具列表里、仍然能被 agent 绑定，但一执行就找不到连接配置。
    # 是个静默的坏。
    #
    # 这段逻辑以前在 lea-tools 的 DELETE /admin/mcp-servers/{id} 里，那条路由连同
    # 另外三组裸奔的 /admin/* 一起删掉了（见 lea_tools/api/admin_mcp.py 的说明），
    # 所以清理责任移到这里。按 tenant_id + mcp_server_id 两个条件删 —— 只按后者的话，
    # 万一 id 在别的租户里被复用就会误删。
    db.query(Tool).filter(
        Tool.tenant_id == s.tenant_id,
        Tool.mcp_server_id == s.id,
    ).delete(synchronize_session=False)

    db.delete(s)
    db.commit()
