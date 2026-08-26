"""Agent 数据库模型。

Agent = 人格 + 挂载技能 + 挂载 KB + 工具 的组合。
Sprint C 升实：avatar/soul_prompt/welcome_message/category/example_questions/sort_order 从硬编码改为 DB 列。
"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String, Text, UniqueConstraint, func

from lea_server.core.tenant import TenantMixin
from lea_server.database import Base


class Agent(TenantMixin, Base):
    """Agent 定义 —— 一个"人格 + 挂载技能 + 挂载 KB + 工具"的组合。"""

    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_agents_tenant_name"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, index=True)
    description = Column(Text, default="")

    # ── 人格 / 展示 ──────────────────────────────────────────
    avatar = Column(String(200), default="")
    welcome_message = Column(Text, default="")
    soul_prompt = Column(Text, default="")
    category = Column(String(50), default="general")
    example_questions = Column(JSON, default=list)
    sort_order = Column(Integer, default=0)

    # ── 运行时配置（Go 引擎读取）─────────────────────────────
    model = Column(String(100), default="")  # LLM 模型名，空=使用租户默认
    temperature = Column(Integer, default=7)  # x10 存储: 7 = 0.7
    max_turns = Column(Integer, default=20)  # agent loop 最大轮次
    max_tokens = Column(Integer, default=4096)  # 单轮最大输出 token
    kb_mode = Column(String(20), default="tool")  # always / tool / off
    memory_mode = Column(String(20), default="always")  # always / tool / off
    timeout_seconds = Column(Integer, default=120)  # 整个 agent 执行超时

    # ── 生命周期 / 权限 ───────────────────────────────────────
    is_system = Column(Boolean, default=False, index=True)
    enabled = Column(Boolean, default=True, index=True)
    created_by = Column(String(100), nullable=True)

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<Agent {self.name} tenant={self.tenant_id} system={self.is_system}>"
