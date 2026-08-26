"""MCP Server 服务 —— CRUD + config 校验。

Sprint A 只做 CRUD 骨架；Sprint E 加：
- 连接生命周期（connect/disconnect on enable/disable）
- 反射 tool → 写 tool 表（source=mcp）
- connection_status 回写
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lea_server.core.tenant import DEFAULT_TENANT_ID
from lea_server.models.mcp_server import (
    MCP_SCOPE_PLATFORM,
    MCP_SCOPE_USER,
    MCP_SCOPE_VALUES,
    MCP_STATUS_UNKNOWN,
    MCP_TRANSPORT_SSE,
    MCP_TRANSPORT_STDIO,
    MCP_TRANSPORT_STREAMABLE_HTTP,
    MCP_TRANSPORT_VALUES,
    MCPServer,
)

logger = structlog.get_logger()


# admin 可通过 PUT 修改的字段
_UI_EDITABLE_COLS = {
    "name",
    "description",
    "transport",
    "config",
    "enabled",
}


def _validate_config_shape(transport: str, config: dict) -> None:
    """基础字段完整性校验 —— 不做真实连接测试，那是 Sprint E。"""
    if transport == MCP_TRANSPORT_STDIO:
        if not config.get("command"):
            raise ValueError("stdio transport 需要 config.command")
    elif transport in (MCP_TRANSPORT_SSE, MCP_TRANSPORT_STREAMABLE_HTTP):
        if not config.get("url"):
            raise ValueError(f"{transport} transport 需要 config.url")


class MCPServerService:
    """MCP Server CRUD + 生命周期骨架。"""

    def __init__(self, db: Session):
        self.db = db

    # ── 查询 ──────────────────────────────────────────────────

    def get_or_404(self, mcp_id: int) -> MCPServer:
        obj = self.db.query(MCPServer).filter(MCPServer.id == mcp_id).first()
        if obj is None:
            raise ValueError(f"MCPServer id={mcp_id} not found")
        return obj

    def get_by_name(
        self,
        tenant_id: str,
        name: str,
        *,
        owner_scope: str = MCP_SCOPE_PLATFORM,
        owner_user_id: str | None = None,
    ) -> MCPServer | None:
        q = (
            self.db.query(MCPServer)
            .filter(
                MCPServer.tenant_id == tenant_id,
                MCPServer.name == name,
                MCPServer.owner_scope == owner_scope,
            )
        )
        if owner_scope == MCP_SCOPE_USER:
            q = q.filter(MCPServer.owner_user_id == owner_user_id)
        return q.first()

    def list_servers(
        self,
        *,
        owner_scope: str | None = None,
        owner_user_id: str | None = None,
        transport: str | None = None,
        enabled: bool | None = None,
        search: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[MCPServer], int]:
        q = self.db.query(MCPServer)
        if owner_scope:
            q = q.filter(MCPServer.owner_scope == owner_scope)
        if owner_user_id is not None:
            q = q.filter(MCPServer.owner_user_id == owner_user_id)
        if transport:
            q = q.filter(MCPServer.transport == transport)
        if enabled is not None:
            q = q.filter(MCPServer.enabled == enabled)
        if search:
            like = f"%{search}%"
            q = q.filter((MCPServer.name.ilike(like)) | (MCPServer.description.ilike(like)))
        total = q.count()
        rows = q.order_by(MCPServer.id).offset((page - 1) * size).limit(size).all()
        return rows, total

    # ── 写入 ──────────────────────────────────────────────────

    def create(
        self,
        *,
        tenant_id: str,
        name: str,
        transport: str,
        config: dict,
        description: str = "",
        enabled: bool = True,
        owner_scope: str = MCP_SCOPE_PLATFORM,
        owner_user_id: str | None = None,
        created_by: str | None = None,
    ) -> MCPServer:
        if transport not in MCP_TRANSPORT_VALUES:
            raise ValueError(f"invalid transport: {transport}")
        if owner_scope not in MCP_SCOPE_VALUES:
            raise ValueError(f"invalid owner_scope: {owner_scope}")
        if owner_scope == MCP_SCOPE_USER and not owner_user_id:
            raise ValueError("owner_scope=user 必须提供 owner_user_id")
        if owner_scope == MCP_SCOPE_PLATFORM and owner_user_id:
            raise ValueError("owner_scope=platform 不允许提供 owner_user_id")
        _validate_config_shape(transport, config or {})

        server = MCPServer(
            tenant_id=tenant_id,
            name=name,
            description=description,
            transport=transport,
            config=config or {},
            enabled=enabled,
            owner_scope=owner_scope,
            owner_user_id=owner_user_id,
            connection_status=MCP_STATUS_UNKNOWN,
            created_by=created_by,
        )
        self.db.add(server)
        try:
            self.db.commit()
            self.db.refresh(server)
        except IntegrityError as exc:
            self.db.rollback()
            raise ValueError(
                f"mcp server name '{name}' already exists in tenant '{tenant_id}' / {owner_scope}"
            ) from exc
        logger.info(
            "mcp_server_created",
            id=server.id, name=name, scope=owner_scope, transport=transport,
        )
        return server

    def update(self, mcp_id: int, payload: dict) -> MCPServer:
        server = self.get_or_404(mcp_id)
        forbidden = set(payload.keys()) - _UI_EDITABLE_COLS
        if forbidden:
            raise ValueError(
                f"只允许改 {sorted(_UI_EDITABLE_COLS)}；禁止改 {sorted(forbidden)}"
            )

        # transport 或 config 改了 → 重校验 shape
        new_transport = payload.get("transport", server.transport)
        new_config = payload.get("config", server.config or {})
        if new_transport not in MCP_TRANSPORT_VALUES:
            raise ValueError(f"invalid transport: {new_transport}")
        _validate_config_shape(new_transport, new_config or {})

        for k, v in payload.items():
            setattr(server, k, v)
        try:
            self.db.commit()
            self.db.refresh(server)
        except IntegrityError as exc:
            self.db.rollback()
            raise ValueError("mcp server name conflict") from exc
        logger.info("mcp_server_updated", id=server.id, name=server.name)
        return server

    def set_enabled(self, mcp_id: int, enabled: bool) -> MCPServer:
        server = self.get_or_404(mcp_id)
        server.enabled = enabled
        self.db.commit()
        self.db.refresh(server)
        logger.info("mcp_server_enabled_toggled", id=server.id, enabled=enabled)
        return server

    def delete(self, mcp_id: int) -> None:
        server = self.get_or_404(mcp_id)
        # Sprint E: 这里要先 disconnect + 从 tool 表清 source=mcp 且 server_id 匹配的 tool
        self.db.delete(server)
        self.db.commit()
        logger.info("mcp_server_deleted", id=mcp_id)

    # ── 序列化 ────────────────────────────────────────────────

    @staticmethod
    def to_dict(server: MCPServer) -> dict[str, Any]:
        return {
            "id": server.id,
            "tenant_id": server.tenant_id,
            "name": server.name,
            "description": server.description or "",
            "owner_scope": server.owner_scope,
            "owner_user_id": server.owner_user_id,
            "transport": server.transport,
            "config": server.config or {},
            "enabled": server.enabled,
            "connection_status": server.connection_status,
            "last_connected_at": server.last_connected_at.isoformat() if server.last_connected_at else None,
            "last_error": server.last_error,
            "created_by": server.created_by,
            "created_at": server.created_at.isoformat() if server.created_at else None,
            "updated_at": server.updated_at.isoformat() if server.updated_at else None,
        }
