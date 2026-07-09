"""Admin Teams API — 专家团 CRUD。

POST   /admin/teams                  创建 Team（自动创建门面 Agent）
GET    /admin/teams                  列表
GET    /admin/teams/{id}             详情（含成员）
PUT    /admin/teams/{id}             更新配置
DELETE /admin/teams/{id}             删除
POST   /admin/teams/{id}/members     添加成员
DELETE /admin/teams/{id}/members/{agent_id}  移除成员
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from lea_server.core.deps import admin_required
from lea_server.core.tenant import DEFAULT_TENANT_ID
from lea_server.database import get_db
from lea_server.models.agent import Agent
from lea_server.models.agent_team import AgentTeam, AgentTeamMember
from lea_server.models.models import User

router = APIRouter(prefix="/admin/teams", tags=["admin-teams"])


class TeamCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    avatar: str = ""
    strategy: str = Field("parallel", pattern="^(parallel|sequential|debate)$")
    decomposer_prompt: str = ""
    synthesizer_prompt: str = ""
    max_subtasks: int = Field(5, ge=1, le=20)


class TeamUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = None
    avatar: str | None = None
    strategy: str | None = Field(None, pattern="^(parallel|sequential|debate)$")
    decomposer_prompt: str | None = None
    synthesizer_prompt: str | None = None
    max_subtasks: int | None = Field(None, ge=1, le=20)


class MemberAddRequest(BaseModel):
    agent_id: int
    role_description: str = ""
    sort_order: int = 0


def _team_to_dict(team: AgentTeam, agent: Agent, members=None) -> dict:
    d = {
        "id": team.id,
        "agent_id": team.agent_id,
        "name": agent.name,
        "description": agent.description or "",
        "avatar": agent.avatar or "",
        "strategy": team.strategy,
        "decomposer_prompt": team.decomposer_prompt or "",
        "synthesizer_prompt": team.synthesizer_prompt or "",
        "max_subtasks": team.max_subtasks,
    }
    if members is not None:
        d["members"] = members
    return d


@router.post("")
def create_team(
    req: TeamCreateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(admin_required),
):
    """创建 Team（自动创建门面 Agent + AgentTeam 行）。"""
    from lea_server.services.agent_service import AgentService

    tenant_id = getattr(user, "tenant_id", None) or DEFAULT_TENANT_ID
    svc = AgentService(db)
    agent = svc.create(
        tenant_id=tenant_id,
        name=req.name,
        description=req.description,
        created_by=user.user_id,
        avatar=req.avatar,
        category="team",
    )
    db.flush()

    team = AgentTeam(
        agent_id=agent.id,
        strategy=req.strategy,
        decomposer_prompt=req.decomposer_prompt,
        synthesizer_prompt=req.synthesizer_prompt,
        max_subtasks=req.max_subtasks,
    )
    db.add(team)
    db.commit()
    db.refresh(team)
    return _team_to_dict(team, agent)


@router.get("")
def list_teams(
    db: Session = Depends(get_db),
    _: User = Depends(admin_required),
):
    """列出所有 Team。"""
    rows = db.query(AgentTeam, Agent).join(Agent, Agent.id == AgentTeam.agent_id).all()
    return {"items": [_team_to_dict(t, a) for t, a in rows]}


@router.get("/{team_id}")
def get_team(
    team_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(admin_required),
):
    """获取 Team 详情（含成员）。"""
    team = db.query(AgentTeam).filter(AgentTeam.id == team_id).first()
    if not team:
        raise HTTPException(404, "team not found")
    agent = db.query(Agent).filter(Agent.id == team.agent_id).first()

    member_rows = (
        db.query(AgentTeamMember, Agent)
        .join(Agent, Agent.id == AgentTeamMember.member_agent_id)
        .filter(AgentTeamMember.team_id == team_id)
        .order_by(AgentTeamMember.sort_order, AgentTeamMember.id)
        .all()
    )
    members = [
        {
            "id": m.id,
            "agent_id": a.id,
            "name": a.name,
            "avatar": a.avatar or "",
            "role_description": m.role_description or "",
            "sort_order": m.sort_order,
            "enabled": m.enabled,
        }
        for m, a in member_rows
    ]
    return _team_to_dict(team, agent, members=members)


@router.put("/{team_id}")
def update_team(
    team_id: int,
    req: TeamUpdateRequest,
    db: Session = Depends(get_db),
    _: User = Depends(admin_required),
):
    """更新 Team 配置。"""
    team = db.query(AgentTeam).filter(AgentTeam.id == team_id).first()
    if not team:
        raise HTTPException(404, "team not found")
    agent = db.query(Agent).filter(Agent.id == team.agent_id).first()

    data = req.model_dump(exclude_none=True)
    agent_fields = {"name", "description", "avatar"}
    for k in agent_fields & data.keys():
        setattr(agent, k, data.pop(k))
    for k, v in data.items():
        if hasattr(team, k):
            setattr(team, k, v)

    db.commit()
    db.refresh(team)
    return _team_to_dict(team, agent)


@router.delete("/{team_id}")
def delete_team(
    team_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(admin_required),
):
    """删除 Team（CASCADE 删门面 Agent）。"""
    team = db.query(AgentTeam).filter(AgentTeam.id == team_id).first()
    if not team:
        raise HTTPException(404, "team not found")
    agent = db.query(Agent).filter(Agent.id == team.agent_id).first()
    db.delete(team)
    if agent:
        db.delete(agent)
    db.commit()
    return {"message": "已删除"}


@router.post("/{team_id}/members")
def add_member(
    team_id: int,
    req: MemberAddRequest,
    db: Session = Depends(get_db),
    _: User = Depends(admin_required),
):
    """添加成员到 Team。"""
    team = db.query(AgentTeam).filter(AgentTeam.id == team_id).first()
    if not team:
        raise HTTPException(404, "team not found")
    agent = db.query(Agent).filter(Agent.id == req.agent_id).first()
    if not agent:
        raise HTTPException(404, "agent not found")
    if agent.id == team.agent_id:
        raise HTTPException(400, "不能把 Team 自己添加为成员")

    existing = (
        db.query(AgentTeamMember)
        .filter(AgentTeamMember.team_id == team_id, AgentTeamMember.member_agent_id == req.agent_id)
        .first()
    )
    if existing:
        existing.role_description = req.role_description
        existing.sort_order = req.sort_order
        existing.enabled = True
    else:
        existing = AgentTeamMember(
            team_id=team_id,
            member_agent_id=req.agent_id,
            role_description=req.role_description,
            sort_order=req.sort_order,
        )
        db.add(existing)

    db.commit()
    return {"member_id": existing.id, "agent_id": req.agent_id}


@router.delete("/{team_id}/members/{agent_id}")
def remove_member(
    team_id: int,
    agent_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(admin_required),
):
    """移除成员。"""
    m = (
        db.query(AgentTeamMember)
        .filter(AgentTeamMember.team_id == team_id, AgentTeamMember.member_agent_id == agent_id)
        .first()
    )
    if not m:
        raise HTTPException(404, "member not found")
    db.delete(m)
    db.commit()
    return {"message": "已移除"}
