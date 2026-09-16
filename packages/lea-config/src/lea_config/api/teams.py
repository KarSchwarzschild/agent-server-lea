"""Agent Team CRUD 路由 — 统一 /teams 路径，Auth 控制权限。

AgentTeam 无 TenantMixin，通过 agent.tenant_id 间接约束。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_
from sqlalchemy.orm import Session

from lea_authkit import Actor, require_actor
from lea_config.database import get_db
from lea_config.models.agent import Agent
from lea_config.models.agent import visible_to as agent_visible_to
from lea_config.models.team import AgentTeam, AgentTeamMember

router = APIRouter(prefix="/teams", tags=["teams"])


# ---------- Schemas ----------

class TeamCreate(BaseModel):
    agent_id: int
    strategy: str = "round_robin"
    decomposer_prompt: str = ""
    synthesizer_prompt: str = ""
    max_subtasks: int = 5


class TeamUpdate(BaseModel):
    strategy: Optional[str] = None
    decomposer_prompt: Optional[str] = None
    synthesizer_prompt: Optional[str] = None
    max_subtasks: Optional[int] = None


class MemberCreate(BaseModel):
    member_agent_id: int
    role: str = "worker"
    name: str = ""


# ---------- Helpers ----------

def _row_to_dict(obj) -> dict:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def _ensure_team_tenant(db: Session, team: AgentTeam, actor: Actor) -> None:
    """通过 team.agent_id → Agent.tenant_id 链路校验租户归属。"""
    if actor.is_platform_admin:
        return
    agent = db.query(Agent).filter(Agent.id == team.agent_id).first()
    if not agent or agent.tenant_id != actor.tenant_id:
        raise HTTPException(404, "Team not found")


# ---------- Team CRUD ----------

@router.get("")
def list_teams(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    # 可见性跟 agent 用同一条规则（团 = 团长 agent + 成员，见 agent_visible_to）：
    # 本租户共享 + 自己私有 + **全局平台层(default)**。以前这里是 Agent.tenant_id ==
    # actor.tenant_id，没有 default 全局逃逸——于是官方公共专家团对别的租户不可见，跟
    # agent 列表当初漏掉全局层是同一个 bug。平台管理员看全部。
    q = db.query(AgentTeam).join(Agent, Agent.id == AgentTeam.agent_id)
    if not actor.is_platform_admin:
        # enabled 过滤跟 list_agents 一致：禁用团长 agent = 这个团也不再露出（隐藏测试团
        # 就靠禁用团长）。少了它，禁用了团长、团却还挂在专家团目录里。
        q = q.filter(Agent.enabled.is_(True), and_(*agent_visible_to(actor.tenant_id, actor.user_id)))
    total = q.count()
    teams = q.order_by(AgentTeam.id).offset((page - 1) * page_size).limit(page_size).all()

    # 补上团长 agent 的展示信息 —— _row_to_dict(team) 只有 strategy/prompts，没有名字/头像，
    # 前端拿到没法渲染。顺带带上成员数，目录里能显示"N 位专家"。
    items = []
    for t in teams:
        d = _row_to_dict(t)
        ag = db.query(Agent).filter(Agent.id == t.agent_id).first()
        if ag:
            d["name"] = ag.name
            d["description"] = ag.description or ""
            d["avatar"] = ag.avatar or "\U0001f916"
            d["category"] = ag.category or "general"
            d["enabled"] = bool(ag.enabled)
        d["member_count"] = (
            db.query(AgentTeamMember).filter(AgentTeamMember.team_id == t.id).count()
        )
        items.append(d)
    return {"total": total, "items": items}


@router.post("", status_code=201)
def create_team(
    body: TeamCreate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    # 验证 agent 属于当前租户
    if not actor.is_platform_admin:
        agent = db.query(Agent).filter(Agent.id == body.agent_id).first()
        if not agent or agent.tenant_id != actor.tenant_id:
            raise HTTPException(404, "Agent not found")
    team = AgentTeam(**body.model_dump())
    db.add(team)
    db.commit()
    db.refresh(team)
    return _row_to_dict(team)


@router.put("/{team_id}")
def update_team(
    team_id: int,
    body: TeamUpdate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    team = db.query(AgentTeam).filter(AgentTeam.id == team_id).first()
    if not team:
        raise HTTPException(404, "Team not found")
    _ensure_team_tenant(db, team, actor)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(team, k, v)
    db.commit()
    db.refresh(team)
    return _row_to_dict(team)


@router.delete("/{team_id}", status_code=204)
def delete_team(
    team_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    team = db.query(AgentTeam).filter(AgentTeam.id == team_id).first()
    if not team:
        raise HTTPException(404, "Team not found")
    _ensure_team_tenant(db, team, actor)
    db.delete(team)
    db.commit()


# ---------- Member CRUD ----------

@router.get("/{team_id}/members")
def list_members(
    team_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    team = db.query(AgentTeam).filter(AgentTeam.id == team_id).first()
    if not team:
        raise HTTPException(404, "Team not found")
    _ensure_team_tenant(db, team, actor)
    members = db.query(AgentTeamMember).filter(AgentTeamMember.team_id == team_id).all()
    return {"items": [_row_to_dict(member) for member in members]}


@router.post("/{team_id}/members", status_code=201)
def add_member(
    team_id: int,
    body: MemberCreate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    team = db.query(AgentTeam).filter(AgentTeam.id == team_id).first()
    if not team:
        raise HTTPException(404, "Team not found")
    _ensure_team_tenant(db, team, actor)
    member = AgentTeamMember(team_id=team_id, **body.model_dump())
    db.add(member)
    db.commit()
    db.refresh(member)
    return _row_to_dict(member)


@router.delete("/{team_id}/members/{member_id}", status_code=204)
def remove_member(
    team_id: int,
    member_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    team = db.query(AgentTeam).filter(AgentTeam.id == team_id).first()
    if not team:
        raise HTTPException(404, "Team not found")
    _ensure_team_tenant(db, team, actor)
    member = db.query(AgentTeamMember).filter(
        AgentTeamMember.id == member_id,
        AgentTeamMember.team_id == team_id,
    ).first()
    if not member:
        raise HTTPException(404, "Member not found")
    db.delete(member)
    db.commit()
