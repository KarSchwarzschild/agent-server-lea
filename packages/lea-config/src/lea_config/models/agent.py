"""Agent 模型。"""

from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String, Text, UniqueConstraint, func

from lea_config.database import Base, OwnerMixin, TenantMixin


class Agent(OwnerMixin, TenantMixin, Base):
    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_agents_tenant_name"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, index=True)
    description = Column(Text, default="")

    avatar = Column(String(200), default="")
    welcome_message = Column(Text, default="")
    soul_prompt = Column(Text, default="")
    category = Column(String(50), default="general")
    example_questions = Column(JSON, default=list)
    sort_order = Column(Integer, default=0)

    model = Column(String(100), default="")
    temperature = Column(Integer, default=7)
    max_turns = Column(Integer, default=20)
    max_tokens = Column(Integer, default=4096)
    kb_mode = Column(String(20), default="tool")
    memory_mode = Column(String(20), default="always")
    timeout_seconds = Column(Integer, default=120)

    is_system = Column(Boolean, default=False, index=True)
    enabled = Column(Boolean, default=True, index=True)
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
