"""内部 API — Go Agent Engine 专用，无鉴权。

一次性返回 agent 完整运行时配置，避免 N+1 调用。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from lea_server.database import get_db
from lea_server.models.agent import Agent
from lea_server.models.agent_binding import AgentKbBinding, AgentSkillBinding, AgentToolBinding
from lea_server.models.skill import Skill
from lea_server.models.tool import Tool

router = APIRouter(prefix="/internal", tags=["internal"])


@router.get("/agent-config/{agent_id}")
def get_agent_full_config(agent_id: int, db: Session = Depends(get_db)):
    """返回 agent 完整运行时配置 — Go 引擎 session init 时调一次。"""

    agent = db.query(Agent).filter(Agent.id == agent_id, Agent.enabled.is_(True)).first()
    if not agent:
        raise HTTPException(404, "agent not found or disabled")

    # Skills: 已启用的 binding + skill 详情
    skill_rows = (
        db.query(Skill, AgentSkillBinding.priority)
        .join(AgentSkillBinding, AgentSkillBinding.skill_id == Skill.id)
        .filter(
            AgentSkillBinding.agent_id == agent_id,
            AgentSkillBinding.enabled.is_(True),
            Skill.status == "published",
        )
        .order_by(AgentSkillBinding.priority)
        .all()
    )
    skills = [
        {
            "id": s.id,
            "name": s.name,
            "display_name": s.display_name,
            "description": s.description,
            "system_prompt": s.system_prompt or "",
            "model_tier": s.model_tier,
            "required_tools": s.required_tools or [],
            "priority": priority,
        }
        for s, priority in skill_rows
    ]

    # KBs: 绑定的知识库 ID + 检索参数
    kb_rows = (
        db.query(AgentKbBinding)
        .filter(AgentKbBinding.agent_id == agent_id, AgentKbBinding.enabled.is_(True))
        .all()
    )
    kbs = [
        {"kb_id": kb.kb_id, "strategy": kb.strategy, "top_k": kb.top_k, "weight": kb.weight}
        for kb in kb_rows
    ]

    # Tools: 直接绑定的工具 + schema
    tool_rows = (
        db.query(Tool)
        .join(AgentToolBinding, AgentToolBinding.tool_id == Tool.id)
        .filter(
            AgentToolBinding.agent_id == agent_id,
            AgentToolBinding.enabled.is_(True),
            Tool.is_enabled.is_(True),
        )
        .all()
    )
    tools = [
        {"name": t.name, "display_name": t.display_name, "input_schema": t.input_schema}
        for t in tool_rows
    ]

    # Team config (可选)
    team = None
    try:
        from lea_server.models.agent_team import AgentTeam, AgentTeamMember
        team_row = db.query(AgentTeam).filter(AgentTeam.agent_id == agent_id).first()
        if team_row:
            members = db.query(AgentTeamMember).filter(AgentTeamMember.team_id == team_row.id).all()
            team = {
                "strategy": team_row.strategy,
                "decomposer_prompt": team_row.decomposer_prompt or "",
                "synthesizer_prompt": team_row.synthesizer_prompt or "",
                "max_subtasks": team_row.max_subtasks,
                "members": [
                    {"agent_id": m.member_agent_id, "role": m.role, "name": m.name}
                    for m in members
                ],
            }
    except Exception:
        pass

    return {
        "agent": {
            "id": agent.id,
            "name": agent.name,
            "tenant_id": agent.tenant_id,
            "soul_prompt": agent.soul_prompt or "",
            "model": agent.model or "",
            "temperature": (agent.temperature or 7) / 10.0,
            "max_turns": agent.max_turns or 20,
            "max_tokens": agent.max_tokens or 4096,
            "kb_mode": agent.kb_mode or "tool",
            "memory_mode": agent.memory_mode or "always",
            "timeout_seconds": agent.timeout_seconds or 120,
            "welcome_message": agent.welcome_message or "",
        },
        "skills": skills,
        "knowledge_bases": kbs,
        "tools": tools,
        "team": team,
    }
