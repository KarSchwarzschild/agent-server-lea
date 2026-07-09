"""用户侧 Agents 端点 —— Sprint A · A6：从 stub 切到 DB 真实 agents。

契约：
- GET  /agents                     列表（当前租户下 enabled=True 的 agents，包含 super-agent）
- GET  /agents/{id}                单个（id=0 兜底为 tenant super-agent，向后兼容旧前端）
- GET  /agents/default             当前默认 agent id（Sprint A：直接返回 tenant super-agent）
- PUT  /agents/default             设置默认（Sprint A：收下但不落库，等 user_preferences 表）
- DELETE /agents/default           清除默认（同上）
- GET  /agents/categories          Plaza 分类 map（Sprint A：{general: 通用}）
- GET  /agents/super-agent-categories  Selector 分类 map（同上）

Agent 模型是骨架（Ph1）：avatar/category/example_questions 等字段暂用常量填充，
Sprint C 补 schema 时再从 DB 取。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from lea_server.core.deps import get_current_user
from lea_server.core.tenant import DEFAULT_TENANT_ID
from lea_server.database import get_db
from lea_server.models.agent import Agent
from lea_server.models.models import User
from lea_server.services.agent_service import AgentService

router = APIRouter(prefix="/agents", tags=["agents"])


def _resolve_tenant_super_agent(db: Session, tenant_id: str) -> Agent | None:
    """拉当前租户的 super-agent（seed 时保证有；理论上不应为 None）。"""
    return (
        db.query(Agent)
        .filter(
            Agent.name == "super-agent",
            Agent.tenant_id == tenant_id,
            Agent.is_system == True,  # noqa: E712
        )
        .first()
    )


def _agent_to_dict(agent: Agent, current_username: str | None) -> dict:
    """DB Agent → 前端 selector/plaza 期望的形状。"""
    is_super = agent.is_system and agent.name == "super-agent"
    return {
        "id": agent.id,
        "name": "Super Agent" if is_super else agent.name,
        "description": agent.description or "",
        "avatar": agent.avatar or "🤖",
        "welcome_message": agent.welcome_message or "",
        "soul_prompt": agent.soul_prompt or "",
        "category": agent.category or "general",
        "category_label": agent.category or "通用",
        "is_system": bool(agent.is_system),
        "is_mine": bool(
            current_username
            and agent.created_by
            and agent.created_by == current_username
        ),
        "is_enabled": bool(agent.enabled),
        "sort_order": agent.sort_order or 0,
        "allowed_skills": None,
        "example_questions": agent.example_questions or [],
    }


class DefaultAgentBody(BaseModel):
    """PUT /default 请求体，兼容前端传 agent_id。"""

    agent_id: int | None = None


@router.get("")
def list_agents(
    scope: str = "all",
    category: str | None = None,
    search: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """列表 —— 当前租户下 enabled=True 的 agents。

    scope: 'all' / 'mine' / 'system'（Sprint A 简单实现，忽略高级过滤）
    """
    svc = AgentService(db)
    rows, _ = svc.list_agents(enabled=True, search=search, page=1, size=200)

    tenant_id = getattr(user, "tenant_id", None) or DEFAULT_TENANT_ID
    rows = [a for a in rows if a.tenant_id == tenant_id]

    if scope == "mine":
        rows = [a for a in rows if a.created_by == user.username]
    elif scope == "system":
        rows = [a for a in rows if a.is_system]

    return {"items": [_agent_to_dict(a, user.username) for a in sorted(rows, key=lambda a: (a.sort_order or 0, a.id))]}


@router.get("/categories")
def get_categories(_: User = Depends(get_current_user)):
    """Plaza 用的分类映射（Sprint A：只有 general）。"""
    return {"general": "通用"}


@router.get("/super-agent-categories")
def get_super_agent_categories(_: User = Depends(get_current_user)):
    """Selector 用的分类映射（Sprint A：同 categories）。"""
    return {"general": "通用"}


@router.get("/default")
def get_default_agent(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """当前用户的默认 agent_id。

    Sprint A 没有 per-user 偏好表 → 直接返回 tenant 的 super-agent id；
    Sprint C 加 user_preferences 表后再切到用户设置的值。
    """
    tenant_id = getattr(user, "tenant_id", None) or DEFAULT_TENANT_ID
    super_agent = _resolve_tenant_super_agent(db, tenant_id)
    return {"agent_id": super_agent.id if super_agent else None}


@router.put("/default")
def set_default_agent(
    body: DefaultAgentBody,
    _: User = Depends(get_current_user),
):
    """设置默认 agent（Sprint A：no-op，只回显请求 agent_id）。"""
    return {"agent_id": body.agent_id}


@router.delete("/default")
def clear_default_agent(_: User = Depends(get_current_user)):
    """清除默认 agent。"""
    return {"agent_id": None}


@router.get("/{agent_id}")
def get_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """获取单个 agent。

    id=0（旧 stub 前端硬编码）→ 兜底为 tenant super-agent，避免升级期 404。
    """
    tenant_id = getattr(user, "tenant_id", None) or DEFAULT_TENANT_ID

    if agent_id == 0:
        agent = _resolve_tenant_super_agent(db, tenant_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="super-agent not seeded")
        return _agent_to_dict(agent, user.username)

    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if agent is None or agent.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="agent not found")
    return _agent_to_dict(agent, user.username)


# ══════════════════════════════════════════════════════════════
#  用户侧 Agent CRUD
# ══════════════════════════════════════════════════════════════


class UserAgentCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    avatar: str = ""
    welcome_message: str = ""
    soul_prompt: str = ""
    category: str = "general"
    example_questions: list[str] = []


class UserAgentUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = None
    avatar: str | None = None
    welcome_message: str | None = None
    soul_prompt: str | None = None
    category: str | None = None
    example_questions: list[str] | None = None


def _require_mine(agent: Agent, user: User) -> None:
    """校验 agent 属于当前用户。"""
    if agent.is_system:
        raise HTTPException(status_code=403, detail="不能操作系统 Agent")
    if agent.created_by != user.user_id:
        raise HTTPException(status_code=403, detail="只能操作自己创建的 Agent")


@router.post("")
def create_agent(
    req: UserAgentCreateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """创建用户自己的 Agent。"""
    tenant_id = getattr(user, "tenant_id", None) or DEFAULT_TENANT_ID
    svc = AgentService(db)
    try:
        agent = svc.create(
            tenant_id=tenant_id,
            name=req.name,
            description=req.description,
            created_by=user.user_id,
            avatar=req.avatar,
            welcome_message=req.welcome_message,
            soul_prompt=req.soul_prompt,
            category=req.category,
            example_questions=req.example_questions,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return _agent_to_dict(agent, user.username)


@router.put("/{agent_id}")
def update_agent(
    agent_id: int,
    req: UserAgentUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """编辑自己的 Agent。"""
    tenant_id = getattr(user, "tenant_id", None) or DEFAULT_TENANT_ID
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if agent is None or agent.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="agent not found")
    _require_mine(agent, user)

    svc = AgentService(db)
    data = req.model_dump(exclude_none=True)
    if not data:
        return _agent_to_dict(agent, user.username)
    agent = svc.update(agent_id, data)
    db.commit()
    return _agent_to_dict(agent, user.username)


@router.delete("/{agent_id}")
def delete_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """删除自己的 Agent。"""
    tenant_id = getattr(user, "tenant_id", None) or DEFAULT_TENANT_ID
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if agent is None or agent.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="agent not found")
    _require_mine(agent, user)

    svc = AgentService(db)
    svc.delete(agent_id)
    db.commit()
    return {"message": "已删除"}


# ══════════════════════════════════════════════════════════════
#  用户侧 Skill Binding
# ══════════════════════════════════════════════════════════════


class UserSkillBindRequest(BaseModel):
    skill_id: int
    priority: int = Field(100, ge=0, le=9999)


@router.get("/{agent_id}/skills")
def list_agent_skills(
    agent_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """查看 agent 已绑的 skill 列表。"""
    from lea_server.models.agent_binding import AgentSkillBinding
    from lea_server.models.skill import Skill

    tenant_id = getattr(user, "tenant_id", None) or DEFAULT_TENANT_ID
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if agent is None or agent.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="agent not found")

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
def bind_skill_to_agent(
    agent_id: int,
    req: UserSkillBindRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """绑定 skill 到自己的 agent（校验 skill 可见性）。"""
    from lea_server.models.skill import Skill
    from lea_server.services.agent_binding_service import AgentBindingService

    tenant_id = getattr(user, "tenant_id", None) or DEFAULT_TENANT_ID
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if agent is None or agent.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="agent not found")
    _require_mine(agent, user)

    skill = db.query(Skill).filter(Skill.id == req.skill_id).first()
    if skill is None:
        raise HTTPException(status_code=404, detail="skill not found")

    # 角色可见性校验
    if skill.allowed_roles:
        user_role_codes = {r.code for r in (user.roles or [])}
        if not (set(skill.allowed_roles) & user_role_codes):
            raise HTTPException(status_code=403, detail="无权使用该 Skill")

    binding_svc = AgentBindingService(db)
    binding = binding_svc.upsert_skill_binding(
        agent_id, req.skill_id, priority=req.priority,
    )
    db.commit()
    return {"binding_id": binding.id, "skill_id": req.skill_id, "priority": req.priority}


@router.delete("/{agent_id}/skills/{skill_id}")
def unbind_skill_from_agent(
    agent_id: int,
    skill_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """解绑 skill。"""
    from lea_server.services.agent_binding_service import AgentBindingService

    tenant_id = getattr(user, "tenant_id", None) or DEFAULT_TENANT_ID
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if agent is None or agent.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="agent not found")
    _require_mine(agent, user)

    binding_svc = AgentBindingService(db)
    removed = binding_svc.remove_skill_binding(agent_id, skill_id)
    if not removed:
        raise HTTPException(status_code=404, detail="binding not found")
    db.commit()
    return {"message": "已解绑"}


# ══════════════════════════════════════════════════════════════
#  用户侧 KB Binding
# ══════════════════════════════════════════════════════════════


class UserKbBindRequest(BaseModel):
    kb_id: int
    strategy: str = Field("hybrid", pattern="^(semantic|keyword|hybrid)$")
    top_k: int = Field(5, ge=1, le=50)


@router.get("/{agent_id}/kbs")
def list_agent_kbs(
    agent_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """查看 agent 已绑的知识库列表。"""
    from lea_server.models.agent_binding import AgentKbBinding
    from lea_server.models.knowledge import KnowledgeBase

    tenant_id = getattr(user, "tenant_id", None) or DEFAULT_TENANT_ID
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if agent is None or agent.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="agent not found")

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
def bind_kb_to_agent(
    agent_id: int,
    req: UserKbBindRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """绑定知识库到自己的 agent。"""
    from lea_server.models.knowledge import KnowledgeBase
    from lea_server.services.agent_binding_service import AgentBindingService

    tenant_id = getattr(user, "tenant_id", None) or DEFAULT_TENANT_ID
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if agent is None or agent.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="agent not found")
    _require_mine(agent, user)

    kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == req.kb_id).first()
    if kb is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    # 校验 KB 归属：只能绑自己的私有 KB 或公共 KB
    if kb.visibility == "private" and kb.owner != user.user_id:
        raise HTTPException(status_code=403, detail="无权使用该知识库")

    binding_svc = AgentBindingService(db)
    binding = binding_svc.upsert_kb_binding(
        agent_id, req.kb_id, strategy=req.strategy, top_k=req.top_k,
    )
    db.commit()
    return {"binding_id": binding.id, "kb_id": req.kb_id, "strategy": req.strategy}


@router.delete("/{agent_id}/kbs/{kb_id}")
def unbind_kb_from_agent(
    agent_id: int,
    kb_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """解绑知识库。"""
    from lea_server.services.agent_binding_service import AgentBindingService

    tenant_id = getattr(user, "tenant_id", None) or DEFAULT_TENANT_ID
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if agent is None or agent.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="agent not found")
    _require_mine(agent, user)

    binding_svc = AgentBindingService(db)
    removed = binding_svc.remove_kb_binding(agent_id, kb_id)
    if not removed:
        raise HTTPException(status_code=404, detail="binding not found")
    db.commit()
    return {"message": "已解绑"}
