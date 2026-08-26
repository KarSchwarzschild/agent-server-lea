"""Agent Team 模型。"""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func

from lea_config.database import Base


class AgentTeam(Base):
    __tablename__ = "agent_teams"
    __table_args__ = (
        UniqueConstraint("agent_id", name="uq_agent_teams_agent_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    strategy = Column(String(50), default="round_robin")
    decomposer_prompt = Column(Text, default="")
    synthesizer_prompt = Column(Text, default="")
    max_subtasks = Column(Integer, default=5)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class AgentTeamMember(Base):
    __tablename__ = "agent_team_members"

    id = Column(Integer, primary_key=True, autoincrement=True)
    team_id = Column(Integer, ForeignKey("agent_teams.id", ondelete="CASCADE"), nullable=False, index=True)
    member_agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False, index=True)
    role = Column(String(50), default="worker")
    name = Column(String(100), default="")
    created_at = Column(DateTime, default=func.now())
