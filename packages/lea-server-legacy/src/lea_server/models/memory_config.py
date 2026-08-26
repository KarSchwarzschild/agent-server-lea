"""记忆配置模型 — 控制记忆功能的开关和参数。"""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, func

from lea_server.core.tenant import TenantMixin
from lea_server.database import Base


class MemoryConfig(TenantMixin, Base):
    """记忆配置 — 按租户/Agent 粒度控制记忆行为。"""

    __tablename__ = "memory_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, nullable=True, index=True)
    enabled = Column(Boolean, default=True)
    max_memories = Column(Integer, default=200)
    extract_enabled = Column(Boolean, default=True)
    categories = Column(JSON, default=["fact", "preference", "identity", "progress"])
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
