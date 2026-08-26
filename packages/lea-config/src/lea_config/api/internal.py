"""Engine 内部 API — 无鉴权，供引擎拉取完整 Agent 运行配置。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from lea_config.database import get_db
from lea_config.models.agent import Agent
from lea_config.models.binding import AgentKbBinding, AgentSkillBinding, AgentToolBinding
from lea_config.models.llm_provider import LLMProvider
from lea_config.models.skill import Skill
from lea_config.models.team import AgentTeam, AgentTeamMember
from lea_config.models.tool import Tool, exposed_name

router = APIRouter(prefix="/internal", tags=["internal"])


def _row_to_dict(obj) -> dict:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def _context_window_of(provider: LLMProvider, model_name: str) -> int:
    """从 provider.model_limits 里取这个模型的上下文窗口，取不到返回 0。

    0 的含义是"这里没有答案"，交给 lea-agent 兜底（session.contextBudget 会用
    fallbackContextWindow）。**不在这里塞缺省** —— 两边各有一个缺省的话它们迟早
    不一样，而不一样的表现是"压缩时机跟你以为的不同"，那种问题查起来很费劲。

    精确匹配模型名，不做前缀匹配。前缀匹配正是被换掉的那套（Go 里的
    contextWindowFor），它的毛病是模型名多一个后缀就静默落进缺省 —— 而"静默"是
    这一整轮改动要消灭的东西。宁可查不到走兜底，那条路径至少是明确的。
    """
    limits = provider.model_limits or {}
    if not isinstance(limits, dict):
        return 0
    try:
        return max(0, int(limits.get(model_name, 0)))
    except (TypeError, ValueError):
        # 配置里填了非数字。返 0 走兜底，不要让一次手滑把整个会话打挂。
        return 0


def _resolve_super_agent(db: Session, tenant_id: str) -> Agent | None:
    """H2 兜底：AgentID=0 或找不到 → tenant 内 name='super-agent' & is_system。"""
    q = db.query(Agent).filter(
        Agent.name == "super-agent",
        Agent.is_system == True,  # noqa: E712
    )
    if tenant_id:
        q = q.filter(Agent.tenant_id == tenant_id)
    return q.first()


@router.get("/agent-config/{agent_id}")
def get_agent_config(
    agent_id: int,
    tenant_id: str = Query("", description="租户 id；agent_id=0 兜底时用"),
    db: Session = Depends(get_db),
):
    """返回完整 Agent 运行时配置（一次查全）。

    H2：agent_id=0（前端硬编码）或库里查不到时，回退到 tenant super-agent。
    """
    agent: Agent | None = None
    if agent_id > 0:
        agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if agent is None:
        agent = _resolve_super_agent(db, tenant_id)
    if not agent:
        raise HTTPException(404, "Agent not found (and no super-agent seeded)")
    agent_id = agent.id

    # Skills
    #
    # 两个开关都要在这里生效，因为**这是下发的唯一入口** —— 只在列表页过滤等于
    # 「停用了但还在用」：已绑定的 agent 照样会拿到它、照样投递到会话目录、照样
    # 披露给模型。
    #
    #   b.enabled       这个 agent 不再用这个 skill（绑定还留着，随时能开回来）
    #   Skill.enabled   这个 skill 整个停用，所有绑它的 agent 一起断
    #
    # binding_enabled 以前是**收集了没人读**的：这里老实标出来，而 Go 侧
    # ToEngineSkills 全仓零命中 —— 于是绑定级开关一直是失效的。现在在源头就滤掉，
    # 下游拿到的就是「该用的」，不需要每个消费者各记一次这条规则。
    skill_bindings = [
        b
        for b in db.query(AgentSkillBinding).filter_by(agent_id=agent_id).all()
        if b.enabled
    ]
    skill_ids = [b.skill_id for b in skill_bindings]
    skills_raw = (
        db.query(Skill)
        .filter(Skill.id.in_(skill_ids), Skill.enabled.is_(True))
        .all()
        if skill_ids
        else []
    )
    skills_map = {s.id: s for s in skills_raw}
    skills = []
    for b in skill_bindings:
        s = skills_map.get(b.skill_id)
        if s:
            d = _row_to_dict(s)
            d["priority"] = b.priority
            skills.append(d)

    # Knowledge Bases — 只返回绑定的 kb_ids，KB 详情由 lea-rag 管
    kb_bindings = db.query(AgentKbBinding).filter_by(agent_id=agent_id).all()
    kb_ids = [b.kb_id for b in kb_bindings if b.enabled]

    # Tools
    #
    # 两个开关在这里生效，理由跟上面 skills 那段完全一样 —— 这是下发的唯一入口，
    # 只在列表页过滤等于「停用了但还在用」。
    #
    #   b.enabled      这个 agent 不再用这个工具（绑定还留着，随时能开回来）
    #   Tool.enabled   这个工具整个停用，所有绑它的 agent 一起断
    #
    # 两条都是**迁移时掉的**，不是从没有过：老的 lea-server 那版
    # （api/internal.py 的 tool_rows）在 JOIN 里就带着
    # `AgentToolBinding.enabled.is_(True)` 和 `Tool.is_enabled.is_(True)`。
    # 搬到这里时只剩一个 filter_by(agent_id)，于是两级停用一起失效了。
    #
    # 这里**不再下发 binding_enabled**。它以前是收集了没人读的 —— Go 的 ToolDef
    # 压根没有这个字段（runtime/config_loader.go），全仓零命中。把「该不该用」这条
    # 规则留在源头，比发一个标志位再指望每个消费者都记得看它可靠：skills 那段就是
    # 因为标志位没人读才漏了一轮。
    tool_bindings = [
        b
        for b in db.query(AgentToolBinding).filter_by(agent_id=agent_id).all()
        if b.enabled
    ]
    tool_ids = [b.tool_id for b in tool_bindings]
    tools_raw = (
        db.query(Tool)
        .filter(Tool.id.in_(tool_ids), Tool.enabled.is_(True))
        .all()
        if tool_ids
        else []
    )
    tools_map = {t.id: t for t in tools_raw}
    tools = []
    for b in tool_bindings:
        t = tools_map.get(b.tool_id)
        if t:
            d = _row_to_dict(t)
            # 发**给模型看的**名字，不是库里那个裸名。用户私有工具带 my__ 前缀。
            #
            # 这一跳以前是漏的，后果是用户私有工具**结构上调不通**：下发裸名 →
            # 模型调裸名 → 执行侧的 resolve_tool 在 owner_scope != USER 里找 → 404。
            # 而 lea-tools 那个会加前缀的 list_tools_for_session 没有任何调用方
            # （引擎的清单来自这里），所以那半边命名空间一直是失效的。
            #
            # 库里的 name 不受影响 —— 绑定、权限、审计都还是按裸名和 id 走。
            d["name"] = exposed_name(t)
            tools.append(d)

    # Team
    team_row = db.query(AgentTeam).filter_by(agent_id=agent_id).first()
    team = None
    if team_row:
        members = db.query(AgentTeamMember).filter_by(team_id=team_row.id).all()
        team = {
            "strategy": team_row.strategy,
            "decomposer_prompt": team_row.decomposer_prompt,
            "synthesizer_prompt": team_row.synthesizer_prompt,
            "max_subtasks": team_row.max_subtasks,
            "members": [_row_to_dict(m) for m in members],
        }

    # LLM Provider — 匹配 agent.model 对应的 provider
    llm_config = {}
    model_name = agent.model or ""
    if model_name:
        providers = db.query(LLMProvider).filter(
            LLMProvider.tenant_id == agent.tenant_id,
            LLMProvider.enabled == True,
        ).all()
        matched = None
        for p in providers:
            supported = [m.strip() for m in (p.models or "").split(",") if m.strip()]
            if model_name in supported:
                matched = p
                break
        if not matched:
            matched = next((p for p in providers if p.is_default), None)
        if matched:
            llm_config = {
                "llm_base_url": matched.base_url,
                "llm_api_key": matched.api_key,
                # 窗口跟 base_url / api_key 一起走：它们都是"这个模型怎么调"的一部分，
                # 而且都由上面这次 provider 匹配得出。
                "model_context_window": _context_window_of(matched, model_name),
            }

    return {
        "agent": _row_to_dict(agent),
        "kb_ids": kb_ids,
        "skills": skills,
        "tools": tools,
        "team": team,
        "llm": llm_config,
    }
