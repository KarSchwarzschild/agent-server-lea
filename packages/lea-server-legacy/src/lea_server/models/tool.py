"""Tool 数据库模型 —— 平台工具元数据持久化。

契约：
- 内置 tool（source=builtin）由 lea_graph/tools/ 代码定义 BaseTool 实例；启动时 seed 到表里。
  内置 tool 只能改「管理层元数据」（display_name / category / is_enabled），不能删。
- MCP tool（source=mcp）由 mcp_servers 配置动态发现；source_config 存 {server_id, remote_name}。
- API tool（source=api）由 admin 手工建（未来）；source_config 存 HTTP 调用参数。

TOOL 定义与运行时实例的分工：
- 定义（本表）：admin 层「能管理什么」— 元数据 · 启停 · 授权基础
- 实例（tools/registry.py）：运行时「能执行什么」— BaseTool 对象 · 调用适配
两者用 name 关联；registry 只暴露 is_enabled=True 的 tool。
"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String, Text, UniqueConstraint, func

from lea_server.core.tenant import TenantMixin
from lea_server.database import Base


# source 枚举（应用层校验）
TOOL_SOURCE_BUILTIN = "builtin"
TOOL_SOURCE_MCP = "mcp"
TOOL_SOURCE_API = "api"
TOOL_SOURCE_VALUES = (TOOL_SOURCE_BUILTIN, TOOL_SOURCE_MCP, TOOL_SOURCE_API)


class Tool(TenantMixin, Base):
    """Tool 定义 —— 平台可用工具的元数据。"""

    __tablename__ = "tools"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_tools_tenant_name"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, index=True)  # 对应 BaseTool.name
    display_name = Column(String(200), nullable=False)
    description = Column(Text, default="")
    category = Column(String(50), default="general", index=True)

    # ── 来源 ─────────────────────────────────────────────────
    source = Column(String(20), nullable=False, default=TOOL_SOURCE_BUILTIN, index=True)
    source_config = Column(JSON, default=dict)  # mcp: {server_id, remote_name}; api: {...}
    input_schema = Column(JSON, default=dict)   # 从 BaseTool.args_schema 或 MCP 反射

    # ── 生命周期 / 权限 ───────────────────────────────────────
    is_enabled = Column(Boolean, default=True, index=True)
    is_builtin = Column(Boolean, default=False, index=True)  # 内置只能禁不能删
    created_by = Column(String(100), nullable=True)

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<Tool {self.name} tenant={self.tenant_id} source={self.source} enabled={self.is_enabled}>"
