"""Engine 内部 API — 无鉴权，供引擎拉取完整 Agent 运行配置。"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lea_config.database import get_db
from lea_config.models.agent import Agent
from lea_config.models.binding import (
    AgentKbBinding,
    AgentMcpBinding,
    AgentSkillBinding,
    AgentToolBinding,
)
from lea_config.models.knowledge import KnowledgeBase
from lea_config.models.llm_provider import LLMProvider
from lea_config.models.mcp_server import MCPServer
from lea_config.models.mcp_server import visible_to as mcp_visible_to
from lea_config.models.skill import Skill
from lea_config.models.skill import visible_to as skill_visible_to
from lea_config.models.team import AgentTeam, AgentTeamMember
from lea_config.models.tool import Tool, exposed_name, visible_to

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


def _ensure_super_agent(db: Session, tenant_id: str) -> Agent | None:
    if not tenant_id:
        return None
    agent = _resolve_super_agent(db, tenant_id)
    if agent:
        return agent

    agent = Agent(
        tenant_id=tenant_id,
        name="super-agent",
        description="默认通用专家",
        model=os.getenv("LEA_MODEL_CHAT", "qwen3-max"),
        is_system=True,
        enabled=True,
    )
    db.add(agent)
    try:
        db.commit()
        db.refresh(agent)
        return agent
    except IntegrityError:
        db.rollback()
        return _resolve_super_agent(db, tenant_id)


def _tenant_llm_fallback(model_name: str) -> dict:
    base_url = os.getenv("LEA_TENANT_API_BASE", "").rstrip("/")
    api_key = os.getenv("LEA_TENANT_API_KEY", "")
    if not base_url or not api_key or not model_name:
        return {}
    if not base_url.endswith("/v1"):
        base_url += "/v1"
    return {"llm_base_url": base_url, "llm_api_key": api_key}


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
        agent = _ensure_super_agent(db, tenant_id)
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

    # MCP server 绑定 —— 展开成它同步出的工具行，并进 tools。
    #
    # 粒度是整个 server（跟会话级挂载一致）：绑一个 server = 用它当前同步出的全部工具，
    # server 事后 sync 出新工具会自动跟上，不用回来改绑定。
    #
    # **不按 user 过滤可见性**：跟上面 tool_bindings 那段同理 —— 可见性在 bind_mcp 那一步
    # 校验过了，agent-config 是引擎内部拉的整包配置。私有 server 混进共享 agent 由分享闸
    # （agents.py::_share_blockers）在上架时硬拦，不在这里。
    #
    # 按 tool id 去重：同一个工具既被逐个绑（agent_tool_bindings）又随 server 绑进来时，
    # 只留一份。留先来的那份（逐个绑的），跟会话级 MergeAttachments"碰撞保留已有"一致。
    mcp_ids = [
        b.mcp_server_id
        for b in db.query(AgentMcpBinding).filter_by(agent_id=agent_id).all()
        if b.enabled
    ]
    if mcp_ids:
        seen_tool_ids = {t["id"] for t in tools}
        servers = (
            db.query(MCPServer)
            .filter(MCPServer.id.in_(mcp_ids), MCPServer.enabled.is_(True))
            .all()
        )
        for srv in servers:
            rows = (
                db.query(Tool)
                .filter(Tool.mcp_server_id == srv.id, Tool.enabled.is_(True))
                .all()
            )
            for t in rows:
                if t.id in seen_tool_ids:
                    continue
                seen_tool_ids.add(t.id)
                d = _row_to_dict(t)
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
        else:
            llm_config = _tenant_llm_fallback(model_name)

    return {
        "agent": _row_to_dict(agent),
        "kb_ids": kb_ids,
        "skills": skills,
        "tools": tools,
        "team": team,
        "llm": llm_config,
    }


# ── 会话级挂载的解析 ───────────────────────────────────────────────────────
#
# 用户在**单个会话里**临时挂上的资源（migrations/017 的 session_attachments）。
# 那张表归 lea-agent 写和读 —— 它是会话状态，跟 session_plans / agent_messages 一类，
# 不是配置。lea-config 在这里只做一件事：**把 id 还原成定义，并且按归属过滤**。
#
# 分工的理由跟 tools / mcp_servers 一样：谁拥有这张表，谁定义它的语义。会话状态归
# 引擎，资源定义归配置服务。
#
# ## 跟 agent 绑定的区别（别混）
#
#     agent_tool_bindings   agent 定义的一部分，所有使用者相同，改它 = 改这个 agent
#     session_attachments   只属于一个会话，别人用同一个 agent 完全不受影响
#
# 所以共享 agent 的发布门（api/agents.py 的 _share_blockers）查的是绑定，跟挂载无关。


class AttachmentRef(BaseModel):
    kind: str          # tool | kb | skill（mcp_server 是下一批）
    ref_id: int


class ResolveAttachmentsBody(BaseModel):
    tenant_id: str
    user_id: str = ""
    items: list[AttachmentRef] = []


@router.post("/resolve-attachments")
def resolve_attachments(body: ResolveAttachmentsBody, db: Session = Depends(get_db)):
    """把挂载记录还原成引擎能直接用的配置。

    ## 够不着的**静默丢弃**，不报错

    挂载行没有外键（ref_id 是多态的），而且资源随时可能被删掉、被取消共享、或者用户
    被移出租户。如果这里对失效项报错，后果是**用户的老会话直接打不开** —— 一个跟他
    无关的删除操作，让一整段对话变成打不开的东西。少一个工具远比这个轻。

    但也不能真的悄无声息：失效项进 `dropped` 一起返回，引擎打一行日志。否则"我昨天
    挂的工具今天不见了"在任何地方都查不到。

    ## 返回形状必须跟 /internal/agent-config 的 tools 一致

    引擎两边用的是**同一个** ToolDef 结构体去解。差一个键名就是静默失效 —— 那正是
    `schema_json` 那个 bug 的形状（tag 写成 input_schema，json.Decode 静默丢弃，
    模型拿到一个空 schema 然后全靠猜）。所以这里同样走 _row_to_dict + exposed_name，
    不另起一套。
    """
    tools_out: list[dict] = []
    skills_out: list[dict] = []
    kb_ids: list[int] = []
    dropped: list[dict] = []

    tool_ids = [i.ref_id for i in body.items if i.kind == "tool"]
    skill_ids = [i.ref_id for i in body.items if i.kind == "skill"]
    mcp_ids = [i.ref_id for i in body.items if i.kind == "mcp_server"]
    kb_refs = [i.ref_id for i in body.items if i.kind == "kb"]
    for i in body.items:
        if i.kind not in ("tool", "kb", "skill", "mcp_server"):
            # 认不出的 kind 不算错误：kind 是 VARCHAR 就是为了让新资源不必先改所有
            # 读端（见 migrations/017 的说明）。老版本的 lea-config 碰到 mcp_server
            # 就该跳过，而不是让整个会话建不起来。
            dropped.append({"kind": i.kind, "ref_id": i.ref_id, "reason": "unknown kind"})

    if tool_ids:
        rows = (
            db.query(Tool)
            .filter(
                Tool.id.in_(tool_ids),
                Tool.enabled.is_(True),
                # 可见性判据只有一份，见 models/tool.py::visible_to 的说明
                *visible_to(body.tenant_id, body.user_id or None),
            )
            .all()
        )
        found = {t.id: t for t in rows}
        for tid in tool_ids:
            t = found.get(tid)
            if t is None:
                dropped.append({
                    "kind": "tool", "ref_id": tid,
                    "reason": "not found / disabled / not visible to this user",
                })
                continue
            d = _row_to_dict(t)
            d["name"] = exposed_name(t)   # 发给模型的名字，用户私有工具带 my__
            tools_out.append(d)

    if skill_ids:
        rows = (
            db.query(Skill)
            .filter(
                Skill.id.in_(skill_ids),
                # Skill.enabled 是**整个 skill 停用**，所有绑它的 agent 一起断
                # （见 models/skill.py 那列的注释）。挂载也必须过它，否则"停用了但
                # 还能挂上用"—— 而那正是绑定链路上刚修完的同一个坑。
                Skill.enabled.is_(True),
                *skill_visible_to(body.tenant_id, body.user_id or None),
            )
            .all()
        )
        found = {s.id: s for s in rows}
        for sid in skill_ids:
            s = found.get(sid)
            if s is None:
                dropped.append({
                    "kind": "skill", "ref_id": sid,
                    "reason": "not found / disabled / not visible to this user",
                })
                continue
            # 形状跟 /internal/agent-config 的 skills 一致（同一个 Go SkillDef 去解）。
            # priority 那边来自绑定行，挂载没有绑定 —— 留零值，引擎只用它排序。
            skills_out.append(_row_to_dict(s))

    if mcp_ids:
        # 挂一个 MCP server = 挂它同步出来的**全部**工具行。
        #
        # 粒度是整个 server，跟主流做法一致：MCP 的心智模型是"接一个服务"，而用户在
        # 挂之前并不知道那个服务有哪几个工具（那要 sync 之后才知道）。
        #
        # ## 归属按**工具行**判，不按 server 判
        #
        # 两者今天一致（sync 让工具行继承 server 的归属）。但它们可以短暂不一致 ——
        # server 归属改了、还没重 sync 的那个窗口。那时候按 server 判会放出一批归属
        # 还是旧的工具行，而**真正被执行的是工具行**。所以判据落在它上面。
        #
        # 顺带这也让"server 可见但某几个工具被单独停用"这种情况自然正确。
        servers = (
            db.query(MCPServer)
            .filter(
                MCPServer.id.in_(mcp_ids),
                MCPServer.enabled.is_(True),
                *mcp_visible_to(body.tenant_id, body.user_id or None),
            )
            .all()
        )
        found_srv = {s.id: s for s in servers}

        for mid in mcp_ids:
            srv = found_srv.get(mid)
            if srv is None:
                dropped.append({
                    "kind": "mcp_server", "ref_id": mid,
                    "reason": "not found / disabled / not visible to this user",
                })
                continue

            rows = (
                db.query(Tool)
                .filter(
                    Tool.mcp_server_id == srv.id,
                    Tool.enabled.is_(True),
                    *visible_to(body.tenant_id, body.user_id or None),
                )
                .all()
            )
            if not rows:
                # server 看得见但一个工具都没有 —— 几乎总是"还没 sync 过"。
                # 这不是错误（会话照常建），但必须报出来：否则用户挂了个 server、
                # 模型什么都没多，而任何地方都查不到为什么。
                dropped.append({
                    "kind": "mcp_server", "ref_id": mid,
                    "reason": "server 可见但没有可用的工具行（大概还没 sync）",
                })
                continue

            for t in rows:
                d = _row_to_dict(t)
                d["name"] = exposed_name(t)
                tools_out.append(d)

    if kb_refs:
        rows = (
            db.query(KnowledgeBase)
            .filter(
                KnowledgeBase.id.in_(kb_refs),
                KnowledgeBase.tenant_id == body.tenant_id,
                KnowledgeBase.enabled.is_(True),
            )
            .all()
        )
        found = {k.id: k for k in rows}
        for kid in kb_refs:
            k = found.get(kid)
            # 私有库只有主人能用 —— 跟 api/agents.py 绑定 kb 时那条判据一致。
            # kb 用的是 visibility/owner 这套更老的编码，不是 owner_scope 三段式；
            # 统一它是另一件事，这里先把两套都判对。
            if k is None or (k.visibility == "private" and k.owner != body.user_id):
                dropped.append({
                    "kind": "kb", "ref_id": kid,
                    "reason": "not found / disabled / not visible to this user",
                })
                continue
            kb_ids.append(k.id)

    return {"tools": tools_out, "skills": skills_out, "kb_ids": kb_ids, "dropped": dropped}


# ── 挂载的展示信息 ────────────────────────────────────────────────────────
#
# 界面上那排 chip（"你挂了 天气查询 ✕ / PDF处理 ✕"）要的是名字，而挂载行里只有
# {kind, ref_id}。这个端点把 id 换成人能读的标签。
#
# ## 为什么不复用 /resolve-attachments
#
# 那个是给**引擎**用的，形状被三件事绑住，都跟展示冲突：
#
#     mcp_server 被展平成 N 个工具   → 丢了"哪个 ref_id 产生的"，chip 画不出来
#     skill 返回整段 SKILL.md        → 一个 chip 传几 KB 正文，纯浪费
#     失效项进 dropped 另一个数组     → 前端要自己把两个数组缝回原来的顺序
#
# 硬改那个形状会动到引擎建会话那条路 —— 那是主链路。加一个只读端点便宜得多。
#
# ## 失效项**留在列表里**，标 available=false
#
# 跟 resolve 那边的"静默丢弃"相反，而这是故意的：那边要保证会话能建起来，这边要
# 让用户看见"你挂的这个东西现在用不了"。全丢掉的话 chip 会无声消失，用户只会觉得
# 自己记错了；标出来他才知道该去摘掉或者找人恢复。


class DescribeAttachmentsBody(BaseModel):
    tenant_id: str
    user_id: str = ""
    items: list[AttachmentRef] = []


# 每种 kind 的 (模型, 可见性判据, 有没有 display_name)。
#
# 表驱动而不是四段 if：加一种资源只需要多一行，而漏掉一种的表现是那类 chip 永远
# 显示"未知"——比报错难发现。mcp_server / knowledge_bases 没有 display_name 列
# （见各自的模型），所以这里要记下来，不能对所有 kind 都去取那个属性。
_DESCRIBABLE = {
    "tool": (Tool, visible_to, True),
    "skill": (Skill, skill_visible_to, True),
    "mcp_server": (MCPServer, mcp_visible_to, False),
}


@router.post("/describe-attachments")
def describe_attachments(body: DescribeAttachmentsBody, db: Session = Depends(get_db)):
    """把 {kind, ref_id} 换成 {name, display_name, description, available}。

    顺序跟传进来的一致 —— 前端按用户挂载的顺序渲染，重排会让 chip 位置随每次刷新跳动。

    ⚠️ 这个端点**不能**用来判断"能不能用"以外的任何事。它返回的名字是给人看的，
    工具真正的调用名（带 my__ / mcp_{id}_ 前缀）由 resolve 那条路决定。两处各算一次
    前缀是错的 —— 那正是 remote_tool_name 那个坑的形状。
    """
    out: list[dict] = []

    # 按 kind 分组批量查，避免 N 次单行查询。同一个 kind 里保持传入顺序靠下面
    # 回填时按 ref_id 取，不依赖查询返回的顺序。
    by_kind: dict[str, list[int]] = {}
    for i in body.items:
        by_kind.setdefault(i.kind, []).append(i.ref_id)

    found: dict[tuple[str, int], object] = {}
    for kind, ids in by_kind.items():
        spec = _DESCRIBABLE.get(kind)
        if spec is None:
            continue
        model, vis, _ = spec
        rows = (
            db.query(model)
            .filter(
                model.id.in_(ids),
                model.enabled.is_(True),
                *vis(body.tenant_id, body.user_id or None),
            )
            .all()
        )
        for r in rows:
            found[(kind, r.id)] = r

    # kb 的可见性用的是 visibility/owner 那套老编码，不是 owner_scope 三段式，所以
    # 单独一段。统一两套编码是另一件事（resolve 那边同样分开处理）。
    kb_ids = by_kind.get("kb", [])
    if kb_ids:
        for k in (
            db.query(KnowledgeBase)
            .filter(
                KnowledgeBase.id.in_(kb_ids),
                KnowledgeBase.tenant_id == body.tenant_id,
                KnowledgeBase.enabled.is_(True),
            )
            .all()
        ):
            if k.visibility == "private" and k.owner != body.user_id:
                continue
            found[("kb", k.id)] = k

    for i in body.items:
        row = found.get((i.kind, i.ref_id))
        if row is None:
            # 名字都取不到（删了 / 停用 / 够不着）。给一个能显示的占位，而不是让
            # 前端拿到 null 去猜 —— chip 上写"已失效"比空白强。
            out.append({
                "kind": i.kind, "ref_id": i.ref_id,
                "name": "", "display_name": "", "description": "",
                "available": False,
            })
            continue
        has_display = _DESCRIBABLE.get(i.kind, (None, None, False))[2]
        out.append({
            "kind": i.kind,
            "ref_id": i.ref_id,
            "name": row.name,
            "display_name": (row.display_name or "") if has_display else "",
            "description": row.description or "",
            "available": True,
        })

    return {"attachments": out}
