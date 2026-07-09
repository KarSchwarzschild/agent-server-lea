"""Agent 平台服务 —— CRUD + effective_tools 派生。

契约：
- Agent 是 "人格 + 挂载 skill/KB/tool" 的组合骨架（Sprint A · A3 骨架版）。
- effective_tools = union(skill.required_tools) across 已启用 AgentSkillBinding
  · A5 会再叠加 AgentToolBinding 直接绑的部分。
- is_system 的 Agent（如 super-agent）只允许改「元数据白名单」字段，不能删。
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lea_server.core.tenant import DEFAULT_TENANT_ID
from lea_server.models.agent import Agent
from lea_server.models.agent_binding import AgentSkillBinding, AgentToolBinding
from lea_server.models.skill import Skill
from lea_server.models.tool import Tool

logger = structlog.get_logger()


# is_system Agent 允许通过 PUT 编辑的字段（不允许改 name / is_system）
_SYSTEM_EDITABLE_COLS = {
    "description",
    "enabled",
    "avatar",
    "welcome_message",
    "soul_prompt",
    "category",
    "example_questions",
    "sort_order",
}

# 普通 Agent 允许通过 PUT 编辑的字段
_UI_EDITABLE_COLS = {
    "name",
    "description",
    "enabled",
    "avatar",
    "welcome_message",
    "soul_prompt",
    "category",
    "example_questions",
    "sort_order",
}


class AgentService:
    """Agent CRUD + 派生查询。"""

    def __init__(self, db: Session):
        self.db = db

    # ── 查询 ──────────────────────────────────────────────────

    def get_or_404(self, agent_id: int) -> Agent:
        agent = self.db.query(Agent).filter(Agent.id == agent_id).first()
        if agent is None:
            raise ValueError(f"Agent id={agent_id} not found")
        return agent

    def get_by_name(self, tenant_id: str, name: str) -> Agent | None:
        return (
            self.db.query(Agent)
            .filter(Agent.tenant_id == tenant_id, Agent.name == name)
            .first()
        )

    def list_agents(
        self,
        *,
        enabled: bool | None = None,
        is_system: bool | None = None,
        search: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[Agent], int]:
        q = self.db.query(Agent)
        if enabled is not None:
            q = q.filter(Agent.enabled == enabled)
        if is_system is not None:
            q = q.filter(Agent.is_system == is_system)
        if search:
            like = f"%{search}%"
            q = q.filter((Agent.name.ilike(like)) | (Agent.description.ilike(like)))
        total = q.count()
        rows = q.order_by(Agent.id).offset((page - 1) * size).limit(size).all()
        return rows, total

    # ── 写入 ──────────────────────────────────────────────────

    def create(
        self,
        *,
        tenant_id: str,
        name: str,
        description: str = "",
        enabled: bool = True,
        created_by: str | None = None,
        avatar: str = "",
        welcome_message: str = "",
        soul_prompt: str = "",
        category: str = "general",
        example_questions: list[str] | None = None,
        sort_order: int = 0,
    ) -> Agent:
        agent = Agent(
            tenant_id=tenant_id,
            name=name,
            description=description,
            is_system=False,
            enabled=enabled,
            created_by=created_by,
            avatar=avatar,
            welcome_message=welcome_message,
            soul_prompt=soul_prompt,
            category=category,
            example_questions=example_questions or [],
            sort_order=sort_order,
        )
        self.db.add(agent)
        try:
            self.db.commit()
            self.db.refresh(agent)
        except IntegrityError as exc:
            self.db.rollback()
            raise ValueError(f"agent name '{name}' already exists in tenant '{tenant_id}'") from exc
        logger.info("agent_created", id=agent.id, name=agent.name, tenant=tenant_id)
        return agent

    def update(self, agent_id: int, payload: dict) -> Agent:
        agent = self.get_or_404(agent_id)
        allowed = _SYSTEM_EDITABLE_COLS if agent.is_system else _UI_EDITABLE_COLS
        forbidden = set(payload.keys()) - allowed
        if forbidden:
            role = "system" if agent.is_system else "user"
            raise ValueError(
                f"{role} agent 只允许改 {sorted(allowed)}；禁止改 {sorted(forbidden)}"
            )
        for k, v in payload.items():
            setattr(agent, k, v)
        try:
            self.db.commit()
            self.db.refresh(agent)
        except IntegrityError as exc:
            self.db.rollback()
            raise ValueError("agent name conflict") from exc
        logger.info("agent_updated", id=agent.id, name=agent.name)
        return agent

    def set_enabled(self, agent_id: int, enabled: bool) -> Agent:
        agent = self.get_or_404(agent_id)
        agent.enabled = enabled
        self.db.commit()
        self.db.refresh(agent)
        logger.info("agent_enabled_toggled", id=agent.id, enabled=enabled)
        return agent

    def delete(self, agent_id: int) -> None:
        agent = self.get_or_404(agent_id)
        if agent.is_system:
            raise ValueError("system agent cannot be deleted; disable it instead")
        self.db.delete(agent)
        self.db.commit()
        logger.info("agent_deleted", id=agent_id)

    # ── 派生 ──────────────────────────────────────────────────

    def compute_effective_tools(self, agent_id: int) -> dict[str, Any]:
        """effective_tools = skill 继承 ∪ agent 直接绑（去重, 稳定序）。

        规则：
        - 只算 enabled=True 的 AgentSkillBinding
        - 只算 status='published' 的 Skill（草稿/归档不生效）
        - 只算 enabled=True 的 AgentToolBinding 且 Tool.is_enabled=True（tool 全局停用则 agent 也用不了）
        - skill 继承来的 tool 只是 name（可能是 mcp:xx 前缀等未入表的），直接绑的必然在 tool 表

        返回：{tools, from_skills, from_direct, mcp_placeholders} 便于前端展示"来自哪里"。
        """
        # 1. skill 继承
        skill_rows = (
            self.db.query(Skill.required_tools)
            .join(AgentSkillBinding, AgentSkillBinding.skill_id == Skill.id)
            .filter(
                AgentSkillBinding.agent_id == agent_id,
                AgentSkillBinding.enabled == True,  # noqa: E712
                Skill.status == "published",
            )
            .all()
        )
        from_skills: set[str] = set()
        for (tools,) in skill_rows:
            for name in tools or []:
                from_skills.add(name)

        # 2. 直接绑
        direct_rows = (
            self.db.query(Tool.name)
            .join(AgentToolBinding, AgentToolBinding.tool_id == Tool.id)
            .filter(
                AgentToolBinding.agent_id == agent_id,
                AgentToolBinding.enabled == True,  # noqa: E712
                Tool.is_enabled == True,  # noqa: E712
            )
            .all()
        )
        from_direct: set[str] = {row[0] for row in direct_rows}

        # 3. union
        union = from_skills | from_direct
        # 4. 标记 mcp:* 前缀（未入表的占位符）
        mcp_placeholders = sorted({n for n in from_skills if n.startswith("mcp:")})

        return {
            "tools": sorted(union),
            "from_skills": sorted(from_skills),
            "from_direct": sorted(from_direct),
            "mcp_placeholders": mcp_placeholders,
        }

    def count_bindings(self, agent_id: int) -> dict[str, int]:
        """一次拿 skill/kb 挂载数（agent detail 的摘要）。"""
        from lea_server.models.agent_binding import AgentKbBinding

        skill_count = (
            self.db.query(AgentSkillBinding)
            .filter(AgentSkillBinding.agent_id == agent_id)
            .count()
        )
        kb_count = (
            self.db.query(AgentKbBinding)
            .filter(AgentKbBinding.agent_id == agent_id)
            .count()
        )
        return {"skill_count": skill_count, "kb_count": kb_count}

    # ── 序列化 ────────────────────────────────────────────────

    @staticmethod
    def to_dict(agent: Agent, *, extras: dict[str, Any] | None = None) -> dict[str, Any]:
        base = {
            "id": agent.id,
            "tenant_id": agent.tenant_id,
            "name": agent.name,
            "description": agent.description or "",
            "avatar": agent.avatar or "",
            "welcome_message": agent.welcome_message or "",
            "soul_prompt": agent.soul_prompt or "",
            "category": agent.category or "general",
            "example_questions": agent.example_questions or [],
            "sort_order": agent.sort_order or 0,
            "is_system": agent.is_system,
            "enabled": agent.enabled,
            "created_by": agent.created_by,
            "created_at": agent.created_at.isoformat() if agent.created_at else None,
            "updated_at": agent.updated_at.isoformat() if agent.updated_at else None,
        }
        if extras:
            base.update(extras)
        return base
