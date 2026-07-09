"""MCP Server 数据库模型 —— 平台/用户级 MCP 服务器配置。

契约：
- owner_scope='platform': admin 配置，租户内所有 user 共享
- owner_scope='user': 用户私有 MCP（Sprint A 只留字段不做 API，Sprint E 激活）
- config 直接对齐 langchain-mcp-adapters MultiServerMCPClient 期望的 dict:
  · stdio: {command, args, env}
  · sse / streamable_http: {url, headers}
- connection_status 是"上次连接尝试"的结果快照（Sprint E 由 mcp client 生命周期回写）

多租户：MCPServer 继承 TenantMixin，platform 级的租户隔离靠 tenant_id 兜底。
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)

from lea_server.core.tenant import TenantMixin
from lea_server.database import Base


# transport 枚举（应用层校验，DB 用 String 存）
MCP_TRANSPORT_STDIO = "stdio"
MCP_TRANSPORT_SSE = "sse"
MCP_TRANSPORT_STREAMABLE_HTTP = "streamable_http"
MCP_TRANSPORT_VALUES = (
    MCP_TRANSPORT_STDIO,
    MCP_TRANSPORT_SSE,
    MCP_TRANSPORT_STREAMABLE_HTTP,
)

# owner_scope 枚举
MCP_SCOPE_PLATFORM = "platform"
MCP_SCOPE_USER = "user"
MCP_SCOPE_VALUES = (MCP_SCOPE_PLATFORM, MCP_SCOPE_USER)

# connection_status 枚举
MCP_STATUS_UNKNOWN = "unknown"
MCP_STATUS_CONNECTED = "connected"
MCP_STATUS_ERROR = "error"
MCP_STATUS_VALUES = (MCP_STATUS_UNKNOWN, MCP_STATUS_CONNECTED, MCP_STATUS_ERROR)


class MCPServer(TenantMixin, Base):
    """MCP Server 配置 —— platform 或 user 拥有的一个外部 MCP endpoint。"""

    __tablename__ = "mcp_servers"
    __table_args__ = (
        # 同一租户 · 同一 owner（scope + user）下 name 唯一
        UniqueConstraint(
            "tenant_id", "owner_scope", "owner_user_id", "name",
            name="uq_mcp_servers_tenant_owner_name",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, index=True)
    description = Column(Text, default="")

    # ── 归属 ─────────────────────────────────────────────────
    owner_scope = Column(String(20), nullable=False, default=MCP_SCOPE_PLATFORM, index=True)
    owner_user_id = Column(String(64), nullable=True, index=True)  # scope=user 才填

    # ── 连接配置 ─────────────────────────────────────────────
    transport = Column(String(20), nullable=False, default=MCP_TRANSPORT_STDIO)
    config = Column(JSON, default=dict)  # langchain-mcp-adapters 期望的 dict（不含 transport）

    # ── 生命周期 ─────────────────────────────────────────────
    enabled = Column(Boolean, default=True, index=True)

    # ── 运行时状态快照（Sprint E 回写）───────────────────────
    connection_status = Column(String(20), default=MCP_STATUS_UNKNOWN)
    last_connected_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)

    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        return (
            f"<MCPServer {self.name} tenant={self.tenant_id} "
            f"scope={self.owner_scope} transport={self.transport} enabled={self.enabled}>"
        )
