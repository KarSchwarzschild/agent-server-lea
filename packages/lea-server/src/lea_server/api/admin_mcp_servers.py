"""Admin MCP Server 管理 API（Sprint A · A4，桩版本）。

端点（只做 owner_scope=platform）：
- GET    /admin/mcp-servers                  列表
- GET    /admin/mcp-servers/{id}             详情
- POST   /admin/mcp-servers                  创建 platform MCP 配置
- PUT    /admin/mcp-servers/{id}             改元数据 + 配置
- PATCH  /admin/mcp-servers/{id}/enable      快捷启停
- DELETE /admin/mcp-servers/{id}             删除

Sprint E 会在此挂上 client 生命周期（enable → connect + tool 反射入 tool 表）。
User 侧 CRUD（owner_scope=user）等 Sprint E 单独在 user_mcp_servers.py 建。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from lea_server.core.deps import admin_required
from lea_server.core.tenant import DEFAULT_TENANT_ID, get_tenant
from lea_server.database import get_db
from lea_server.models.mcp_server import (
    MCP_SCOPE_PLATFORM,
    MCP_TRANSPORT_VALUES,
)
from lea_server.models.models import User
from lea_server.services.mcp_server_service import MCPServerService

router = APIRouter(prefix="/admin/mcp-servers", tags=["admin-mcp-servers"])

_require_admin = admin_required


# ── Pydantic 请求体 ──────────────────────────────────────────


class MCPServerCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    transport: str = Field(description=f"one of {MCP_TRANSPORT_VALUES}")
    config: dict = Field(default_factory=dict)
    enabled: bool = True


class MCPServerUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    transport: str | None = None
    config: dict | None = None
    enabled: bool | None = None


class MCPServerEnableRequest(BaseModel):
    enabled: bool


# ── Endpoints ────────────────────────────────────────────────


@router.get("")
async def list_mcp_servers(
    transport: str | None = Query(None),
    enabled: bool | None = Query(None),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    svc = MCPServerService(db)
    rows, total = svc.list_servers(
        owner_scope=MCP_SCOPE_PLATFORM,
        transport=transport,
        enabled=enabled,
        search=search,
        page=page,
        size=size,
    )
    return {
        "items": [MCPServerService.to_dict(s) for s in rows],
        "total": total,
        "page": page,
        "size": size,
    }


@router.get("/{mcp_id}")
async def get_mcp_server(
    mcp_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    svc = MCPServerService(db)
    try:
        server = svc.get_or_404(mcp_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if server.owner_scope != MCP_SCOPE_PLATFORM:
        # 用户私有 MCP 不能通过 admin API 读（Sprint E 用 /api/user/mcp-servers）
        raise HTTPException(status_code=404, detail="MCP server not found (or user-scoped)")
    return MCPServerService.to_dict(server)


@router.post("")
async def create_mcp_server(
    payload: MCPServerCreateRequest,
    admin: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    tenant_id = get_tenant().tenant_id or DEFAULT_TENANT_ID
    svc = MCPServerService(db)
    try:
        server = svc.create(
            tenant_id=tenant_id,
            name=payload.name,
            description=payload.description,
            transport=payload.transport,
            config=payload.config,
            enabled=payload.enabled,
            owner_scope=MCP_SCOPE_PLATFORM,
            owner_user_id=None,
            created_by=getattr(admin, "username", None) or getattr(admin, "user_id", None),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return MCPServerService.to_dict(server)


@router.put("/{mcp_id}")
async def update_mcp_server(
    mcp_id: int,
    payload: MCPServerUpdateRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    svc = MCPServerService(db)
    try:
        server = svc.get_or_404(mcp_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if server.owner_scope != MCP_SCOPE_PLATFORM:
        raise HTTPException(status_code=404, detail="MCP server not found (or user-scoped)")

    data = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not data:
        return MCPServerService.to_dict(server)
    try:
        server = svc.update(mcp_id, data)
    except ValueError as exc:
        msg = str(exc)
        code = 404 if "not found" in msg else 400
        raise HTTPException(status_code=code, detail=msg)
    return MCPServerService.to_dict(server)


@router.patch("/{mcp_id}/enable")
async def toggle_enable(
    mcp_id: int,
    payload: MCPServerEnableRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    svc = MCPServerService(db)
    try:
        server = svc.get_or_404(mcp_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if server.owner_scope != MCP_SCOPE_PLATFORM:
        raise HTTPException(status_code=404, detail="MCP server not found (or user-scoped)")
    server = svc.set_enabled(mcp_id, payload.enabled)
    return MCPServerService.to_dict(server)


@router.delete("/{mcp_id}")
async def delete_mcp_server(
    mcp_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    svc = MCPServerService(db)
    try:
        server = svc.get_or_404(mcp_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if server.owner_scope != MCP_SCOPE_PLATFORM:
        raise HTTPException(status_code=404, detail="MCP server not found (or user-scoped)")
    svc.delete(mcp_id)
    return {"ok": True}
