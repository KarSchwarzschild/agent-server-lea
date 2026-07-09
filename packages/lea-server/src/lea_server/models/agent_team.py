"""Agent Team 数据模型 — 专家团（多 Agent 并行协作）。

AgentTeam 1:1 关联一个 Agent 行（门面），Team 的 UI 展示信息都从 Agent 取。
AgentTeamMember 多对多关联成员 Agent。
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)

from lea_server.database import Base


class AgentTeam(Base):
    __tablename__ = "agent_teams"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(
        Integer,
        ForeignKey("agents.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    strategy = Column(String(20), default="parallel")
    decomposer_prompt = Column(Text, default="")
    synthesizer_prompt = Column(Text, default="")
    max_subtasks = Column(Integer, default=5)

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<AgentTeam id={self.id} agent_id={self.agent_id} strategy={self.strategy}>"


class AgentTeamMember(Base):
    __tablename__ = "agent_team_members"
    __table_args__ = (
        UniqueConstraint("team_id", "member_agent_id", name="uq_team_member"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    team_id = Column(
        Integer,
        ForeignKey("agent_teams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    member_agent_id = Column(
        Integer,
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    role_description = Column(String(200), default="")
    sort_order = Column(Integer, default=0)
    enabled = Column(Boolean, default=True)

    created_at = Column(DateTime, default=func.now())

    def __repr__(self) -> str:
        return f"<AgentTeamMember team={self.team_id} agent={self.member_agent_id}>"
