"""统一 Agent 路由 — 合并 admin + user 端点。

路径 = 资源（/agents），Auth = 权限（scoped_filter + require）。
- 普通用户：scoped_filter 限制只能看到 platform + 自己的 agent
- 管理员：scoped_filter 返回 true()，看到全部
- admin-only 操作靠 _is_admin() 区分
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from lea_authkit import Actor, require_actor, scoped_filter
from lea_config.api._auth_helpers import require_admin
from lea_config.database import (
    DEFAULT_TENANT_ID,
    OWNER_SCOPE_PLATFORM,
    OWNER_SCOPE_USER,
    get_db,
)
from lea_config.models.agent import Agent
from lea_config.models.binding import AgentKbBinding, AgentSkillBinding, AgentToolBinding
from lea_config.models.knowledge import KnowledgeBase
from lea_config.models.skill import Skill
from lea_config.models.team import AgentTeam, AgentTeamMember
from lea_config.models.tool import Tool

router = APIRouter(prefix="/agents", tags=["agents"])


# ── Schemas ────────────────────────────────────────────────

class AgentCreate(BaseModel):
    """统一创建 — 合并 admin 和 user 字段，管理字段 Optional。"""
    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    avatar: str = ""
    welcome_message: str = ""
    soul_prompt: str = ""
    category: str = "general"
    example_questions: list = []
    sort_order: int = 0
    # admin 字段（普通用户忽略）
    model: str = ""
    temperature: int = 7
    max_turns: int = 20
    max_tokens: int = 4096
    kb_mode: str = "tool"
    memory_mode: str = "always"
    timeout_seconds: int = 120
    is_system: bool = False
    enabled: bool = True


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    avatar: Optional[str] = None
    welcome_message: Optional[str] = None
    soul_prompt: Optional[str] = None
    category: Optional[str] = None
    example_questions: Optional[list] = None
    sort_order: Optional[int] = None
    model: Optional[str] = None
    temperature: Optional[int] = None
    max_turns: Optional[int] = None
    max_tokens: Optional[int] = None
    kb_mode: Optional[str] = None
    memory_mode: Optional[str] = None
    timeout_seconds: Optional[int] = None
    enabled: Optional[bool] = None


class DefaultAgentBody(BaseModel):
    agent_id: int | None = None


class BindSkillBody(BaseModel):
    skill_id: int
    priority: int = Field(100, ge=0, le=9999)


class BindKbBody(BaseModel):
    kb_id: int
    strategy: str = Field("hybrid", pattern="^(semantic|keyword|hybrid)$")
    top_k: int = Field(5, ge=1, le=50)


class BindToolBody(BaseModel):
    tool_id: int
    enabled: bool = True


# ── Helpers ────────────────────────────────────────────────

def _is_admin(actor: Actor) -> bool:
    return actor.is_platform_admin or actor.is_tenant_admin


def _resolve_super_agent(db: Session, tenant_id: str) -> Agent | None:
    return (
        db.query(Agent)
        .filter(
            Agent.name == "super-agent",
            Agent.tenant_id == tenant_id,
            Agent.is_system == True,  # noqa: E712
        )
        .first()
    )


def _agent_to_dict(agent: Agent, current_user_id: str | None) -> dict:
    is_super = agent.is_system and agent.name == "super-agent"
    return {
        "id": agent.id,
        "name": "Super Agent" if is_super else agent.name,
        "description": agent.description or "",
        "avatar": agent.avatar or "\U0001f916",
        "welcome_message": agent.welcome_message or "",
        "soul_prompt": agent.soul_prompt or "",
        "category": agent.category or "general",
        "category_label": agent.category or "\u901a\u7528",
        "is_system": bool(agent.is_system),
        "is_mine": bool(
            current_user_id
            and agent.created_by
            and agent.created_by == current_user_id
        ),
        "is_enabled": bool(agent.enabled),
        "sort_order": agent.sort_order or 0,
        "model": agent.model or "",
        "temperature": agent.temperature or 7,
        "max_turns": agent.max_turns or 20,
        "max_tokens": agent.max_tokens or 4096,
        "kb_mode": agent.kb_mode or "tool",
        "memory_mode": agent.memory_mode or "always",
        "timeout_seconds": agent.timeout_seconds or 120,
        "allowed_skills": None,
        "example_questions": agent.example_questions or [],
    }


def _get_agent_or_404(db: Session, agent_id: int, actor: Actor) -> Agent:
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if agent is None:
        raise HTTPException(404, "Agent not found")
    # 跨租户 404，同租户放行
    if not actor.is_platform_admin and agent.tenant_id != actor.tenant_id:
        raise HTTPException(404, "Agent not found")
    return agent


def _require_mine(agent: Agent, actor: Actor) -> None:
    """普通用户只能操作自己创建的非系统 agent。admin 跳过。"""
    if _is_admin(actor):
        return
    if agent.is_system:
        raise HTTPException(403, "\u4e0d\u80fd\u64cd\u4f5c\u7cfb\u7edf Agent")
    if agent.created_by != actor.user_id:
        raise HTTPException(403, "\u53ea\u80fd\u64cd\u4f5c\u81ea\u5df1\u521b\u5efa\u7684 Agent")


# ── Agent CRUD ─────────────────────────────────────────────

@router.get("")
def list_agents(
    scope: str = "all",
    category: str | None = None,
    search: str | None = None,
    enabled: bool | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    is_adm = _is_admin(actor)

    q = db.query(Agent).filter(scoped_filter(actor, Agent))

    # 普通用户只看 enabled 的；admin 可通过 enabled 参数过滤
    if not is_adm:
        q = q.filter(Agent.enabled == True)  # noqa: E712
    elif enabled is not None:
        q = q.filter(Agent.enabled == enabled)

    if search:
        q = q.filter(Agent.name.contains(search))
    if category:
        q = q.filter(Agent.category == category)

    if is_adm:
        # admin 分页
        total = q.count()
        items = (
            q.order_by(Agent.sort_order, Agent.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        rows = items
        result: dict = {"total": total, "items": [_agent_to_dict(a, actor.user_id) for a in rows]}
    else:
        rows = q.order_by(Agent.sort_order, Agent.id).limit(200).all()
        # scope 过滤（内存侧）
        if scope == "mine":
            rows = [a for a in rows if a.created_by == actor.user_id]
        elif scope == "system":
            rows = [a for a in rows if a.is_system]
        result = {"items": [_agent_to_dict(a, actor.user_id) for a in rows]}

    return result


@router.get("/categories")
def get_categories(actor: Actor = Depends(require_actor)):
    return {"general": "\u901a\u7528"}


@router.get("/super-agent-categories")
def get_super_agent_categories(actor: Actor = Depends(require_actor)):
    return {"general": "\u901a\u7528"}


@router.get("/default")
def get_default_agent(
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    """\u5f53\u524d\u7528\u6237\u9ed8\u8ba4 agent \u2014 \u6682\u8fd4\u56de tenant super-agent\u3002"""
    super_agent = _resolve_super_agent(db, actor.tenant_id)
    return {"agent_id": super_agent.id if super_agent else None}


@router.put("/default")
def set_default_agent(body: DefaultAgentBody, actor: Actor = Depends(require_actor)):
    """No-op\uff0c\u7b49 user_preferences \u8868\u3002"""
    return {"agent_id": body.agent_id}


@router.delete("/default")
def clear_default_agent(actor: Actor = Depends(require_actor)):
    return {"agent_id": None}


@router.get("/{agent_id}")
def get_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    """id=0 \u5151\u5e95 super-agent\uff08\u65e7\u524d\u7aef\u786c\u7f16\u7801\uff09\u3002"""
    if agent_id == 0:
        agent = _resolve_super_agent(db, actor.tenant_id)
        if agent is None:
            raise HTTPException(404, "super-agent not seeded")
        return _agent_to_dict(agent, actor.user_id)

    agent = _get_agent_or_404(db, agent_id, actor)
    return _agent_to_dict(agent, actor.user_id)


@router.post("", status_code=201)
def create_agent(
    body: AgentCreate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    # \u67e5\u91cd\u540d
    dup = (
        db.query(Agent)
        .filter(Agent.tenant_id == actor.tenant_id, Agent.name == body.name)
        .first()
    )
    if dup:
        raise HTTPException(400, f"Agent \u540d\u79f0 '{body.name}' \u5df2\u5b58\u5728")

    is_adm = _is_admin(actor)
    data = body.model_dump()

    if not is_adm:
        # \u666e\u901a\u7528\u6237\u5f3a\u5236\u7ea6\u675f
        data["is_system"] = False
        data["enabled"] = True

    agent = Agent(
        tenant_id=actor.tenant_id,
        created_by=actor.user_id,
        owner_scope=OWNER_SCOPE_PLATFORM if data.get("is_system") else OWNER_SCOPE_USER,
        owner_user_id=None if data.get("is_system") else actor.user_id,
        **data,
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return _agent_to_dict(agent, actor.user_id)


@router.put("/{agent_id}")
def update_agent(
    agent_id: int,
    body: AgentUpdate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    agent = _get_agent_or_404(db, agent_id, actor)
    _require_mine(agent, actor)

    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(agent, k, v)
    db.commit()
    db.refresh(agent)
    return _agent_to_dict(agent, actor.user_id)


@router.delete("/{agent_id}")
def delete_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    agent = _get_agent_or_404(db, agent_id, actor)
    _require_mine(agent, actor)

    db.delete(agent)
    db.commit()
    return {"message": "\u5df2\u5220\u9664"}


# ── 共享 / 取消共享 ────────────────────────────────────────
#
# 共享一个 agent = 把 owner_scope 从 user 升到 platform，本租户所有人可用。
#
# ## 为什么要一道门
#
# agent 是个**容器**：它自己没有内容，内容全在它绑的 tool / skill / kb 里。把容器
# 共享出去而里面装着私有资源，等于绕开那些资源自己的可见性。
#
# 真链路实测过这个形状（scripts/my_prefix_e2e.py）：私有工具挂在共享 agent 上，
# 另一个用户的工具清单里确实出现了 my__stock_lookup。
#
# ## 硬拦 vs 软提示：差别在**安全问题还是可用性问题**
#
#     tool / skill / 团队成员    硬拦 —— 这是**安全**问题
#         它们的**定义本身**就是要下发给模型的：名字、描述、完整参数 schema 全部
#         进入别人的对话上下文。执行会被 resolve_tool 挡住，但该泄的在下发时就泄
#         完了 —— 而 schema 的字段名往往就是业务内部结构。挡不住就是真泄漏。
#
#     knowledge_base            只提示 —— 这是**可用性**问题
#         下发的只有 kb_ids，内容要再经过一次 kb_search 才拿得到，而那一跳按归属
#         授权。所以未公开的知识库对别人来说是**用不了**的：同一个 agent，owner
#         自己用能检索到，别人用检索不到，表现不一样。
#
#         这不是"要不要拦住泄漏"，是"要不要提醒作者他共享出去的 agent 对别人是
#         残的"。作者完全可能明知故犯（就想共享一个骨架，让别人自己接知识库），
#         所以只提示不拦。
#
#         ⚠️ 文案说的是"可能无法检索"而不是"无法检索"，因为 lea-rag 那层的归属
#         校验现在是弱的（server_admin_kb.py 的 list_all_kbs 不传 tenant_id 就返
#         全平台）。也就是说**实际行为可能是两头里的任意一头**：按设计别人用不了，
#         按现状别人可能反而搜得到。两种都不好，都该让作者知道，所以文案要留余地，
#         不能斩钉截铁说"别人用不了"给人虚假的安全感。见 [project-auth-gaps]。
#
# ## 拒绝，而不是连带发布
#
# 撞到未发布的资源直接 409 并列出**每一个**阻塞项。不做"顺手一起上架"：那是一个
# 动作改很多东西，而 skill 还有真正的审批流（skill_publish_requests），连带触发
# 等于替审批人做决定。

class ShareBlocker(BaseModel):
    kind: str          # tool / skill / team_member / kb
    id: int
    name: str
    reason: str


def _share_blockers(
    db: Session, agent: Agent
) -> tuple[list[ShareBlocker], list[ShareBlocker]]:
    """返回 (硬阻塞, 软提示)。硬阻塞为空 = 可以共享。

    **两个都列全，不是碰到第一个就返回** —— 用户要的是一张待办清单，一次一个地
    试错很折磨人，尤其绑了十几样东西的 agent。
    """
    blockers: list[ShareBlocker] = []
    warnings: list[ShareBlocker] = []

    tool_rows = (
        db.query(Tool)
        .join(AgentToolBinding, AgentToolBinding.tool_id == Tool.id)
        .filter(AgentToolBinding.agent_id == agent.id)
        .all()
    )
    for t in tool_rows:
        if t.owner_scope == OWNER_SCOPE_USER:
            blockers.append(ShareBlocker(
                kind="tool", id=t.id, name=t.name,
                reason=f"私有工具（属于 {t.owner_user_id}），要先共享到租户",
            ))

    skill_rows = (
        db.query(Skill)
        .join(AgentSkillBinding, AgentSkillBinding.skill_id == Skill.id)
        .filter(AgentSkillBinding.agent_id == agent.id)
        .all()
    )
    for s in skill_rows:
        if s.owner_scope == OWNER_SCOPE_USER:
            blockers.append(ShareBlocker(
                kind="skill", id=s.id, name=s.name,
                reason=f"私有技能（属于 {s.owner_user_id}），走 /skills/{s.id}/publish 上架",
            ))

    # 知识库：**只提示不拦**。见上面"安全 vs 可用性"那段。
    kb_rows = (
        db.query(KnowledgeBase)
        .join(AgentKbBinding, AgentKbBinding.kb_id == KnowledgeBase.id)
        .filter(AgentKbBinding.agent_id == agent.id)
        .all()
    )
    for k in kb_rows:
        if k.visibility == "private":
            warnings.append(ShareBlocker(
                kind="kb", id=k.id, name=k.name,
                reason="这个知识库还没公开，其他人用这个 agent 时可能检索不到它，"
                       "回答会跟你自己用的时候不一样。要让所有人都能用就先把它"
                       "改成 public。",
            ))

    # 专家团成员 —— 硬拦。
    #
    # 不加这条的话前面几条全白做：共享 agent 的团里挂一个**私有**成员 agent，
    # 引擎会用成员的 agent_id 再打一次 /internal/agent-config
    # （runtime/expert_assembly.go:104），成员绑的私有工具照样下发。
    # 容器套容器，可见性要一路传下去。
    team = db.query(AgentTeam).filter_by(agent_id=agent.id).first()
    if team:
        member_ids = [
            m.member_agent_id
            for m in db.query(AgentTeamMember).filter_by(team_id=team.id).all()
        ]
        if member_ids:
            for ma in db.query(Agent).filter(Agent.id.in_(member_ids)).all():
                if ma.owner_scope == OWNER_SCOPE_USER:
                    blockers.append(ShareBlocker(
                        kind="team_member", id=ma.id, name=ma.name,
                        reason=f"专家团成员是私有 agent（属于 {ma.owner_user_id}），"
                               f"要先共享它",
                    ))

    return blockers, warnings


@router.get("/{agent_id}/share-check")
def check_share(
    agent_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    """预检：现在能不能共享，卡在哪，以及有什么要留意的。

    单独一个只读端点，前端可以在"共享"按钮旁边先亮出清单，而不是让用户点一下
    才发现被拒。
    """
    agent = _get_agent_or_404(db, agent_id, actor)
    _require_mine(agent, actor)
    blockers, warnings = _share_blockers(db, agent)
    return {
        "agent_id": agent.id,
        "already_shared": agent.owner_scope == OWNER_SCOPE_PLATFORM,
        "can_share": not blockers,
        "blockers": [b.model_dump() for b in blockers],
        "warnings": [w.model_dump() for w in warnings],
    }


@router.post("/{agent_id}/share")
def share_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    """把自己的私有 agent 升到租户共享档。"""
    agent = _get_agent_or_404(db, agent_id, actor)
    _require_mine(agent, actor)

    # 幂等：已经共享就直接返回，不 400。共享是"达到某个状态"，重复调用该无害 ——
    # 前端重试、双击按钮都会走到这里。
    if agent.owner_scope == OWNER_SCOPE_PLATFORM:
        return {"agent": _agent_to_dict(agent, actor.user_id), "warnings": []}

    blockers, warnings = _share_blockers(db, agent)
    if blockers:
        raise HTTPException(409, {
            "message": f"有 {len(blockers)} 项资源还没共享，agent 不能共享",
            "blockers": [b.model_dump() for b in blockers],
            "warnings": [w.model_dump() for w in warnings],
        })

    agent.owner_scope = OWNER_SCOPE_PLATFORM
    agent.owner_user_id = None
    # is_system 不动。共享 ≠ 系统内置：系统 agent 是平台预置且不可删的，而这是
    # 用户自己建的、共享出来的，他仍然该能改能删。
    db.commit()
    db.refresh(agent)
    # 软提示跟着成功一起返回 —— 共享成功了，但"你有个私有知识库在里面"这件事用户
    # 仍然该知道。丢掉它等于让软提示只在失败时出现，而那时反而没人看。
    return {
        "agent": _agent_to_dict(agent, actor.user_id),
        "warnings": [w.model_dump() for w in warnings],
    }


@router.post("/{agent_id}/unshare")
def unshare_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    """撤回共享，还给原作者。

    共享是个对别人可见的动作。没有回退路径的话它就是一道单向门 —— 用户会因此
    不敢点。所以这个端点跟 share 一起给。

    ⚠️ `created_by` 为空的行拒绝撤回。那些是**种子数据**（库里 agent 1/3/4/5/6 的
    created_by 就是 NULL），撤回等于把平台预置的 agent 塞进当前操作者的私人名下。
    """
    agent = _get_agent_or_404(db, agent_id, actor)
    _require_mine(agent, actor)

    if agent.owner_scope == OWNER_SCOPE_USER:
        return {"agent": _agent_to_dict(agent, actor.user_id)}  # 幂等，同 share

    if not agent.created_by:
        raise HTTPException(
            400, "这个 agent 没有记录创建者（多半是种子数据），不能撤回共享"
        )

    agent.owner_scope = OWNER_SCOPE_USER
    agent.owner_user_id = agent.created_by
    db.commit()
    db.refresh(agent)
    return {"agent": _agent_to_dict(agent, actor.user_id)}


# ── Skill Bindings ─────────────────────────────────────────

@router.get("/{agent_id}/skills")
def list_agent_skills(
    agent_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    _get_agent_or_404(db, agent_id, actor)
    rows = (
        db.query(AgentSkillBinding, Skill)
        .join(Skill, Skill.id == AgentSkillBinding.skill_id)
        .filter(AgentSkillBinding.agent_id == agent_id)
        .order_by(AgentSkillBinding.priority, Skill.id)
        .all()
    )
    return {
        "agent_id": agent_id,
        "skills": [
            {
                "binding_id": b.id,
                "skill_id": s.id,
                "skill_name": s.name,
                "display_name": s.display_name,
                "priority": b.priority,
                "enabled": b.enabled,
            }
            for b, s in rows
        ],
    }


@router.post("/{agent_id}/skills")
def bind_skill(
    agent_id: int,
    body: BindSkillBody,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    agent = _get_agent_or_404(db, agent_id, actor)
    _require_mine(agent, actor)

    # ⚠️ 必须带可见性过滤。以前这里是裸的 `filter(Skill.id == body.skill_id)` ——
    # 只按 id 查，不看租户也不看归属。于是任何人猜一个 id 就能把**别的租户的**
    # skill 绑到自己 agent 上，而下发链路会老老实实从 S3 把包拉下来投进他的会话。
    #
    # 做 skill 市场之后这个洞会变大：市场页会把 id 变成公开信息，不用猜。
    #
    # 三层可见性：全局市场的（tenant='default'）+ 本租户共享的 + 自己私有的。
    # 停用的也不能绑 —— 绑上了也不会下发（见 internal.py），那是个只会让人困惑的状态。
    skill = (
        db.query(Skill)
        .filter(
            Skill.id == body.skill_id,
            Skill.enabled.is_(True),
            Skill.tenant_id.in_([actor.tenant_id, DEFAULT_TENANT_ID]),
            or_(
                Skill.owner_scope == OWNER_SCOPE_PLATFORM,
                and_(
                    Skill.owner_scope == OWNER_SCOPE_USER,
                    Skill.owner_user_id == actor.user_id,
                ),
            ),
        )
        .first()
    )
    if skill is None:
        raise HTTPException(404, "skill not found")

    existing = (
        db.query(AgentSkillBinding)
        .filter_by(agent_id=agent_id, skill_id=body.skill_id)
        .first()
    )
    if existing:
        existing.priority = body.priority
        existing.enabled = True
        db.commit()
        db.refresh(existing)
        return {"binding_id": existing.id, "skill_id": body.skill_id, "priority": body.priority}

    binding = AgentSkillBinding(
        agent_id=agent_id,
        skill_id=body.skill_id,
        priority=body.priority,
        enabled=True,
    )
    db.add(binding)
    db.commit()
    db.refresh(binding)
    return {"binding_id": binding.id, "skill_id": body.skill_id, "priority": body.priority}


@router.delete("/{agent_id}/skills/{skill_id}")
def unbind_skill(
    agent_id: int,
    skill_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    agent = _get_agent_or_404(db, agent_id, actor)
    _require_mine(agent, actor)

    b = (
        db.query(AgentSkillBinding)
        .filter_by(agent_id=agent_id, skill_id=skill_id)
        .first()
    )
    if not b:
        raise HTTPException(404, "binding not found")
    db.delete(b)
    db.commit()
    return {"message": "\u5df2\u89e3\u7ed1"}


# ── KB Bindings ────────────────────────────────────────────

@router.get("/{agent_id}/kbs")
def list_agent_kbs(
    agent_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    _get_agent_or_404(db, agent_id, actor)
    rows = (
        db.query(AgentKbBinding, KnowledgeBase)
        .join(KnowledgeBase, KnowledgeBase.id == AgentKbBinding.kb_id)
        .filter(AgentKbBinding.agent_id == agent_id)
        .all()
    )
    return {
        "agent_id": agent_id,
        "kbs": [
            {
                "binding_id": b.id,
                "kb_id": kb.id,
                "kb_name": kb.name,
                "strategy": b.strategy,
                "top_k": b.top_k,
                "enabled": b.enabled,
            }
            for b, kb in rows
        ],
    }


@router.post("/{agent_id}/kbs")
def bind_kb(
    agent_id: int,
    body: BindKbBody,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    agent = _get_agent_or_404(db, agent_id, actor)
    _require_mine(agent, actor)

    kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == body.kb_id).first()
    if kb is None:
        raise HTTPException(404, "\u77e5\u8bc6\u5e93\u4e0d\u5b58\u5728")
    if kb.visibility == "private" and kb.owner != actor.user_id:
        raise HTTPException(403, "\u65e0\u6743\u4f7f\u7528\u8be5\u77e5\u8bc6\u5e93")

    existing = (
        db.query(AgentKbBinding)
        .filter_by(agent_id=agent_id, kb_id=body.kb_id)
        .first()
    )
    if existing:
        existing.strategy = body.strategy
        existing.top_k = body.top_k
        existing.enabled = True
        db.commit()
        db.refresh(existing)
        return {"binding_id": existing.id, "kb_id": body.kb_id, "strategy": body.strategy}

    binding = AgentKbBinding(
        agent_id=agent_id,
        kb_id=body.kb_id,
        strategy=body.strategy,
        top_k=body.top_k,
        enabled=True,
    )
    db.add(binding)
    db.commit()
    db.refresh(binding)
    return {"binding_id": binding.id, "kb_id": body.kb_id, "strategy": body.strategy}


@router.delete("/{agent_id}/kbs/{kb_id}")
def unbind_kb(
    agent_id: int,
    kb_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    agent = _get_agent_or_404(db, agent_id, actor)
    _require_mine(agent, actor)

    b = (
        db.query(AgentKbBinding)
        .filter_by(agent_id=agent_id, kb_id=kb_id)
        .first()
    )
    if not b:
        raise HTTPException(404, "binding not found")
    db.delete(b)
    db.commit()
    return {"message": "\u5df2\u89e3\u7ed1"}


# ── Tool Bindings (admin-only, 从旧 bindings.py 迁入) ────

@router.post("/{agent_id}/tools")
def bind_tool(
    agent_id: int,
    body: BindToolBody,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    agent = _get_agent_or_404(db, agent_id, actor)
    _require_mine(agent, actor)

    # ⚠️ 必须带可见性过滤 —— 跟 bind_skill 是同一个洞，这里以前一个字的校验都没有：
    # `AgentToolBinding(agent_id=agent_id, **body.model_dump())`，tool_id 直接来自
    # 请求体。于是猜一个 id 就能把**别人的**（别的租户、或同租户别的用户的）工具绑到
    # 自己的 agent 上。
    #
    # 而且工具比 skill 更容易触发：skill 还要从 S3 拉包，工具绑上就下发。泄漏的是
    # 名字 + 描述 + schema，而 schema 里常是业务字段名（lea-tools 的 registry.py
    # 早就写过这句）—— 等于把别人的内部结构送进自己的对话上下文。
    #
    # 执行那一步会被 lea-tools 的 resolve_tool 挡住（它是这条链上唯一带租户+归属的
    # 一环），所以调不动。但「调不动」不等于没事：该泄的在下发时就泄完了。
    #
    # 三层可见性，跟 bind_skill 逐条对齐：
    #   全局平台工具   tenant='default' + platform
    #   本租户共享的   tenant=actor    + platform
    #   自己私有的     tenant=actor    + user + owner_user_id=actor
    #
    # 停用的也不能绑：绑上了也不会下发（internal.py 现在按 Tool.enabled 过滤），
    # 那是个只会让人困惑的状态。
    tool = (
        db.query(Tool)
        .filter(
            Tool.id == body.tool_id,
            Tool.enabled.is_(True),
            Tool.tenant_id.in_([actor.tenant_id, DEFAULT_TENANT_ID]),
            or_(
                Tool.owner_scope == OWNER_SCOPE_PLATFORM,
                and_(
                    Tool.owner_scope == OWNER_SCOPE_USER,
                    Tool.owner_user_id == actor.user_id,
                ),
            ),
        )
        .first()
    )
    if tool is None:
        # 404 而不是 403 —— 403 会确认「这个 tool_id 存在」，而枚举 id 正是这个洞
        # 最省事的用法。跟本文件里其他跨归属的分支保持一致。
        raise HTTPException(404, "tool not found")

    # 重复绑定当成幂等，别 500 在唯一索引上。跟 bind_skill 同形：已存在就把它打开。
    existing = (
        db.query(AgentToolBinding)
        .filter_by(agent_id=agent_id, tool_id=body.tool_id)
        .first()
    )
    if existing:
        existing.enabled = body.enabled
        db.commit()
        db.refresh(existing)
        return {c.name: getattr(existing, c.name) for c in existing.__table__.columns}

    binding = AgentToolBinding(agent_id=agent_id, **body.model_dump())
    db.add(binding)
    db.commit()
    db.refresh(binding)
    return {c.name: getattr(binding, c.name) for c in binding.__table__.columns}


@router.delete("/{agent_id}/tools/{tool_id}")
def unbind_tool(
    agent_id: int,
    tool_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    agent = _get_agent_or_404(db, agent_id, actor)
    _require_mine(agent, actor)

    b = db.query(AgentToolBinding).filter_by(agent_id=agent_id, tool_id=tool_id).first()
    if not b:
        raise HTTPException(404, "Binding not found")
    db.delete(b)
    db.commit()
    return {"message": "\u5df2\u89e3\u7ed1"}


# ── All Bindings List (从旧 bindings.py 迁入) ────────────

@router.get("/{agent_id}/bindings")
def list_bindings(
    agent_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    _get_agent_or_404(db, agent_id, actor)
    skills = db.query(AgentSkillBinding).filter_by(agent_id=agent_id).all()
    kbs = db.query(AgentKbBinding).filter_by(agent_id=agent_id).all()
    tools = db.query(AgentToolBinding).filter_by(agent_id=agent_id).all()

    def _row_to_dict(obj):
        return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}

    return {
        "skills": [_row_to_dict(s) for s in skills],
        "knowledge_bases": [_row_to_dict(k) for k in kbs],
        "tools": [_row_to_dict(t) for t in tools],
    }
