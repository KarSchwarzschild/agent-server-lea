"""Agent 绑定服务 —— agent↔skill / agent↔kb 关系管理。

架构约定（Ph1 · Skill v2）：
- `required_tools` 是 skill 侧的**依赖声明**（挂到 agent 时校验依赖可满足），非运行时权限
- agent 的 KB 绑定既是**权限白名单**（LLM tool_call search_knowledge 时校验 kb_id）
  又是**默认参数源**（top_k/strategy 未传时读默认值）
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lea_server.models.agent import Agent
from lea_server.models.agent_binding import (
    KB_STRATEGY_VALUES,
    AgentKbBinding,
    AgentSkillBinding,
    AgentToolBinding,
)
from lea_server.models.knowledge import KnowledgeBase
from lea_server.models.skill import Skill
from lea_server.models.tool import Tool

logger = structlog.get_logger()


class AgentBindingService:
    """Agent 绑定 CRUD + 校验。"""

    def __init__(self, db: Session):
        self.db = db

    # ── 校验器 ─────────────────────────────────────────────────
    def get_agent_or_404(self, agent_id: int) -> Agent:
        agent = self.db.query(Agent).filter(Agent.id == agent_id).first()
        if agent is None:
            raise ValueError(f"Agent id={agent_id} not found")
        return agent

    def get_skill_or_404(self, skill_id: int) -> Skill:
        skill = self.db.query(Skill).filter(Skill.id == skill_id).first()
        if skill is None:
            raise ValueError(f"Skill id={skill_id} not found")
        return skill

    def get_kb_or_404(self, kb_id: int) -> KnowledgeBase:
        kb = self.db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
        if kb is None:
            raise ValueError(f"KnowledgeBase id={kb_id} not found")
        return kb

    def get_tool_or_404(self, tool_id: int) -> Tool:
        tool = self.db.query(Tool).filter(Tool.id == tool_id).first()
        if tool is None:
            raise ValueError(f"Tool id={tool_id} not found")
        return tool

    # ── Skill Bindings ─────────────────────────────────────────
    def list_skill_bindings(self, agent_id: int) -> list[dict]:
        """列出 agent 挂载的 skill（带 skill 详情，按 priority 升序）。"""
        rows = (
            self.db.query(AgentSkillBinding, Skill)
            .join(Skill, Skill.id == AgentSkillBinding.skill_id)
            .filter(AgentSkillBinding.agent_id == agent_id)
            .order_by(AgentSkillBinding.priority, AgentSkillBinding.id)
            .all()
        )
        return [
            {
                "binding_id": b.id,
                "agent_id": b.agent_id,
                "skill_id": s.id,
                "skill_name": s.name,
                "skill_display_name": s.display_name,
                "skill_status": s.status,
                "skill_is_builtin": s.is_builtin,
                "skill_required_tools": s.required_tools or [],
                "priority": b.priority,
                "enabled": b.enabled,
            }
            for b, s in rows
        ]

    def upsert_skill_binding(
        self,
        agent_id: int,
        skill_id: int,
        *,
        priority: int = 100,
        enabled: bool = True,
    ) -> AgentSkillBinding:
        """幂等挂载：已存在则更新 priority/enabled，不存在则新建。"""
        self.get_agent_or_404(agent_id)
        self.get_skill_or_404(skill_id)

        binding = (
            self.db.query(AgentSkillBinding)
            .filter(
                AgentSkillBinding.agent_id == agent_id,
                AgentSkillBinding.skill_id == skill_id,
            )
            .first()
        )
        if binding is not None:
            binding.priority = priority
            binding.enabled = enabled
        else:
            binding = AgentSkillBinding(
                agent_id=agent_id,
                skill_id=skill_id,
                priority=priority,
                enabled=enabled,
            )
            self.db.add(binding)
            try:
                self.db.flush()
            except IntegrityError:
                # 并发插入 —— 重查取胜方
                self.db.rollback()
                binding = (
                    self.db.query(AgentSkillBinding)
                    .filter(
                        AgentSkillBinding.agent_id == agent_id,
                        AgentSkillBinding.skill_id == skill_id,
                    )
                    .first()
                )
        self.db.flush()
        logger.info(
            "agent_skill_binding_upserted",
            agent_id=agent_id,
            skill_id=skill_id,
            priority=priority,
            enabled=enabled,
        )
        return binding

    def remove_skill_binding(self, agent_id: int, skill_id: int) -> bool:
        n = (
            self.db.query(AgentSkillBinding)
            .filter(
                AgentSkillBinding.agent_id == agent_id,
                AgentSkillBinding.skill_id == skill_id,
            )
            .delete()
        )
        self.db.flush()
        return n > 0

    def replace_skill_bindings(
        self, agent_id: int, items: list[dict[str, Any]]
    ) -> list[dict]:
        """全量替换 agent 的 skill 绑定列表。

        items: [{skill_id, priority?, enabled?}, ...]
        """
        self.get_agent_or_404(agent_id)

        # 清空
        self.db.query(AgentSkillBinding).filter(
            AgentSkillBinding.agent_id == agent_id
        ).delete()

        for item in items:
            skill_id = int(item["skill_id"])
            # 校验 skill 存在（避免残留错误 id）
            self.get_skill_or_404(skill_id)
            self.db.add(
                AgentSkillBinding(
                    agent_id=agent_id,
                    skill_id=skill_id,
                    priority=int(item.get("priority", 100)),
                    enabled=bool(item.get("enabled", True)),
                )
            )
        self.db.flush()
        logger.info(
            "agent_skill_bindings_replaced", agent_id=agent_id, count=len(items)
        )
        return self.list_skill_bindings(agent_id)

    def validate_skill_binding(
        self, agent_id: int, skill_id: int, available_tool_names: set[str]
    ) -> dict[str, Any]:
        """校验 skill 挂载到 agent 前，其 required_tools 是否都能满足。

        Returns:
            {
                "ok": bool,
                "skill_name": str,
                "required_tools": [...],
                "missing_tools": [...],  # required_tools - available_tool_names
                "available_tools": [...] # required_tools ∩ available_tool_names
            }
        """
        skill = self.get_skill_or_404(skill_id)
        self.get_agent_or_404(agent_id)

        required = list(skill.required_tools or [])
        required_set = set(required)
        missing = sorted(required_set - available_tool_names)
        available = sorted(required_set & available_tool_names)

        return {
            "ok": len(missing) == 0,
            "skill_id": skill.id,
            "skill_name": skill.name,
            "required_tools": required,
            "missing_tools": missing,
            "available_tools": available,
        }

    # ── KB Bindings ────────────────────────────────────────────
    def list_kb_bindings(self, agent_id: int) -> list[dict]:
        rows = (
            self.db.query(AgentKbBinding, KnowledgeBase)
            .join(KnowledgeBase, KnowledgeBase.id == AgentKbBinding.kb_id)
            .filter(AgentKbBinding.agent_id == agent_id)
            .order_by(AgentKbBinding.id)
            .all()
        )
        return [
            {
                "binding_id": b.id,
                "agent_id": b.agent_id,
                "kb_id": kb.id,
                "kb_name": kb.name,
                "strategy": b.strategy,
                "top_k": b.top_k,
                "weight": b.weight,
                "enabled": b.enabled,
            }
            for b, kb in rows
        ]

    def upsert_kb_binding(
        self,
        agent_id: int,
        kb_id: int,
        *,
        strategy: str = "hybrid",
        top_k: int = 5,
        weight: float = 1.0,
        enabled: bool = True,
    ) -> AgentKbBinding:
        if strategy not in KB_STRATEGY_VALUES:
            raise ValueError(
                f"strategy must be one of {KB_STRATEGY_VALUES}, got '{strategy}'"
            )
        self.get_agent_or_404(agent_id)
        self.get_kb_or_404(kb_id)

        binding = (
            self.db.query(AgentKbBinding)
            .filter(AgentKbBinding.agent_id == agent_id, AgentKbBinding.kb_id == kb_id)
            .first()
        )
        if binding is not None:
            binding.strategy = strategy
            binding.top_k = top_k
            binding.weight = weight
            binding.enabled = enabled
        else:
            binding = AgentKbBinding(
                agent_id=agent_id,
                kb_id=kb_id,
                strategy=strategy,
                top_k=top_k,
                weight=weight,
                enabled=enabled,
            )
            self.db.add(binding)
            try:
                self.db.flush()
            except IntegrityError:
                self.db.rollback()
                binding = (
                    self.db.query(AgentKbBinding)
                    .filter(
                        AgentKbBinding.agent_id == agent_id,
                        AgentKbBinding.kb_id == kb_id,
                    )
                    .first()
                )
        self.db.flush()
        logger.info(
            "agent_kb_binding_upserted",
            agent_id=agent_id,
            kb_id=kb_id,
            strategy=strategy,
        )
        return binding

    def remove_kb_binding(self, agent_id: int, kb_id: int) -> bool:
        n = (
            self.db.query(AgentKbBinding)
            .filter(AgentKbBinding.agent_id == agent_id, AgentKbBinding.kb_id == kb_id)
            .delete()
        )
        self.db.flush()
        return n > 0

    def replace_kb_bindings(
        self, agent_id: int, items: list[dict[str, Any]]
    ) -> list[dict]:
        self.get_agent_or_404(agent_id)

        self.db.query(AgentKbBinding).filter(
            AgentKbBinding.agent_id == agent_id
        ).delete()

        for item in items:
            kb_id = int(item["kb_id"])
            self.get_kb_or_404(kb_id)
            strategy = item.get("strategy", "hybrid")
            if strategy not in KB_STRATEGY_VALUES:
                raise ValueError(
                    f"strategy must be one of {KB_STRATEGY_VALUES}, got '{strategy}'"
                )
            self.db.add(
                AgentKbBinding(
                    agent_id=agent_id,
                    kb_id=kb_id,
                    strategy=strategy,
                    top_k=int(item.get("top_k", 5)),
                    weight=float(item.get("weight", 1.0)),
                    enabled=bool(item.get("enabled", True)),
                )
            )
        self.db.flush()
        logger.info("agent_kb_bindings_replaced", agent_id=agent_id, count=len(items))
        return self.list_kb_bindings(agent_id)

    # ── Tool Bindings ──────────────────────────────────────────
    def list_tool_bindings(self, agent_id: int) -> list[dict]:
        rows = (
            self.db.query(AgentToolBinding, Tool)
            .join(Tool, Tool.id == AgentToolBinding.tool_id)
            .filter(AgentToolBinding.agent_id == agent_id)
            .order_by(AgentToolBinding.id)
            .all()
        )
        return [
            {
                "binding_id": b.id,
                "agent_id": b.agent_id,
                "tool_id": t.id,
                "tool_name": t.name,
                "tool_display_name": t.display_name,
                "tool_source": t.source,
                "tool_is_builtin": t.is_builtin,
                "enabled": b.enabled,
            }
            for b, t in rows
        ]

    def upsert_tool_binding(
        self,
        agent_id: int,
        tool_id: int,
        *,
        enabled: bool = True,
    ) -> AgentToolBinding:
        self.get_agent_or_404(agent_id)
        self.get_tool_or_404(tool_id)

        binding = (
            self.db.query(AgentToolBinding)
            .filter(
                AgentToolBinding.agent_id == agent_id,
                AgentToolBinding.tool_id == tool_id,
            )
            .first()
        )
        if binding is not None:
            binding.enabled = enabled
        else:
            binding = AgentToolBinding(agent_id=agent_id, tool_id=tool_id, enabled=enabled)
            self.db.add(binding)
            try:
                self.db.flush()
            except IntegrityError:
                self.db.rollback()
                binding = (
                    self.db.query(AgentToolBinding)
                    .filter(
                        AgentToolBinding.agent_id == agent_id,
                        AgentToolBinding.tool_id == tool_id,
                    )
                    .first()
                )
        self.db.flush()
        logger.info(
            "agent_tool_binding_upserted", agent_id=agent_id, tool_id=tool_id, enabled=enabled
        )
        return binding

    def remove_tool_binding(self, agent_id: int, tool_id: int) -> bool:
        n = (
            self.db.query(AgentToolBinding)
            .filter(
                AgentToolBinding.agent_id == agent_id,
                AgentToolBinding.tool_id == tool_id,
            )
            .delete()
        )
        self.db.flush()
        return n > 0

    def replace_tool_bindings(
        self, agent_id: int, items: list[dict[str, Any]]
    ) -> list[dict]:
        self.get_agent_or_404(agent_id)

        self.db.query(AgentToolBinding).filter(
            AgentToolBinding.agent_id == agent_id
        ).delete()

        for item in items:
            tool_id = int(item["tool_id"])
            self.get_tool_or_404(tool_id)
            self.db.add(
                AgentToolBinding(
                    agent_id=agent_id,
                    tool_id=tool_id,
                    enabled=bool(item.get("enabled", True)),
                )
            )
        self.db.flush()
        logger.info("agent_tool_bindings_replaced", agent_id=agent_id, count=len(items))
        return self.list_tool_bindings(agent_id)

    def get_bound_tool_names(self, agent_id: int, *, only_enabled: bool = True) -> list[str]:
        """返回 agent 直接绑的 tool.name 列表（供 effective_tools 派生）。"""
        q = (
            self.db.query(Tool.name)
            .join(AgentToolBinding, AgentToolBinding.tool_id == Tool.id)
            .filter(AgentToolBinding.agent_id == agent_id)
        )
        if only_enabled:
            q = q.filter(AgentToolBinding.enabled == True, Tool.is_enabled == True)  # noqa: E712
        return [row[0] for row in q.all()]

    # ── Supervisor 用 ──────────────────────────────────────────
    def get_bound_skill_ids(self, agent_id: int, *, only_enabled: bool = True) -> list[int]:
        """返回 agent 已绑定且启用的 skill_id 列表（供 supervisor 过滤路由候选池）。"""
        q = self.db.query(AgentSkillBinding.skill_id).filter(
            AgentSkillBinding.agent_id == agent_id
        )
        if only_enabled:
            q = q.filter(AgentSkillBinding.enabled == True)  # noqa: E712
        return [row[0] for row in q.all()]

    def get_bound_kb_ids(self, agent_id: int, *, only_enabled: bool = True) -> list[int]:
        q = self.db.query(AgentKbBinding.kb_id).filter(AgentKbBinding.agent_id == agent_id)
        if only_enabled:
            q = q.filter(AgentKbBinding.enabled == True)  # noqa: E712
        return [row[0] for row in q.all()]

    def get_kb_binding_defaults(self, agent_id: int, kb_id: int) -> dict[str, Any] | None:
        """LLM tool_call 时读默认参数用。返回 None = 该 agent 未绑该 kb。"""
        b = (
            self.db.query(AgentKbBinding)
            .filter(
                AgentKbBinding.agent_id == agent_id,
                AgentKbBinding.kb_id == kb_id,
                AgentKbBinding.enabled == True,  # noqa: E712
            )
            .first()
        )
        if b is None:
            return None
        return {"strategy": b.strategy, "top_k": b.top_k, "weight": b.weight}
