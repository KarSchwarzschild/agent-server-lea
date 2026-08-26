"""Agent 的绑定关系表 —— Agent↔Skill / Agent↔KnowledgeBase / Agent↔Tool 多对多。

- AgentSkillBinding：agent 挂载 skill（priority 控制路由候选顺序）
- AgentKbBinding：agent 挂载 KB（strategy/top_k 是 search_knowledge tool 的默认参数；
  同时作为权限白名单 —— skill/agent LLM 只能查绑定的 KB）
- AgentToolBinding：agent 直接挂 tool（跳过 skill）。effective_tools 是 skill.required_tools 与本表的 union。
"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func

from lea_server.database import Base


# strategy 取值（对齐 knowledge_bases.retrieval_mode）
KB_STRATEGY_SEMANTIC = "semantic"
KB_STRATEGY_KEYWORD = "keyword"
KB_STRATEGY_HYBRID = "hybrid"
KB_STRATEGY_VALUES = (KB_STRATEGY_SEMANTIC, KB_STRATEGY_KEYWORD, KB_STRATEGY_HYBRID)


class AgentSkillBinding(Base):
    """Agent 挂载 Skill 的关系表。"""

    __tablename__ = "agent_skill_bindings"
    __table_args__ = (
        UniqueConstraint("agent_id", "skill_id", name="uq_agent_skill_binding"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    skill_id = Column(Integer, ForeignKey("skills.id", ondelete="CASCADE"), nullable=False, index=True)

    priority = Column(Integer, default=100)  # 数字越小越靠前，多 skill 冲突时的排序
    enabled = Column(Boolean, default=True)  # agent 层面的启停（skill 全局启用但对此 agent 禁用）

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<AgentSkillBinding agent={self.agent_id} skill={self.skill_id} p={self.priority}>"


class AgentKbBinding(Base):
    """Agent 挂载 KB 的关系表 —— 权限白名单 + 默认检索参数。

    LLM tool_call search_knowledge(kb_id, top_k=?, strategy=?) 时：
    - kb_id 必须在此表 agent_id 对应的绑定内（否则拒绝）
    - top_k/strategy 未传时读此表默认值
    """

    __tablename__ = "agent_kb_bindings"
    __table_args__ = (
        UniqueConstraint("agent_id", "kb_id", name="uq_agent_kb_binding"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    kb_id = Column(Integer, ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True)

    strategy = Column(String(20), default=KB_STRATEGY_HYBRID)
    top_k = Column(Integer, default=5)
    weight = Column(Float, default=1.0)  # 多 KB 检索时的加权（可选，Ph2 才用）
    enabled = Column(Boolean, default=True)

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<AgentKbBinding agent={self.agent_id} kb={self.kb_id} strat={self.strategy}>"


class AgentToolBinding(Base):
    """Agent 直接挂 Tool 的关系表 —— 与 skill 继承并集组成 effective_tools。

    - 语义：agent 挑了这个 tool，运行时可用（无需 skill 载体）。
    - 没有 priority：tool 是"能不能用"的授权，不是"选哪个"的排序。
    - effective_tools = union(skill.required_tools for enabled skill) ∪ (enabled tool 绑定)
    """

    __tablename__ = "agent_tool_bindings"
    __table_args__ = (
        UniqueConstraint("agent_id", "tool_id", name="uq_agent_tool_binding"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    tool_id = Column(Integer, ForeignKey("tools.id", ondelete="CASCADE"), nullable=False, index=True)

    enabled = Column(Boolean, default=True)

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<AgentToolBinding agent={self.agent_id} tool={self.tool_id} enabled={self.enabled}>"
