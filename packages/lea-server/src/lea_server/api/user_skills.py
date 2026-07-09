"""用户 Skill API — 查看可用 Skill、创建/管理私有 Skill。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from lea_server.core.deps import get_current_user
from lea_server.database import get_db
from lea_server.models.models import User
from lea_server.models.skill import Skill
from lea_server.services.skill_service import SkillService

router = APIRouter(prefix="/skills", tags=["user-skills"])


def _get_redis(request: Request):
    return getattr(request.app.state, "redis", None)


def _skill_to_dict(skill: Skill) -> dict:
    return {
        "id": skill.id,
        "name": skill.name,
        "display_name": skill.display_name,
        "description": skill.description or "",
        "category": skill.category or "general",
        "execution_mode": skill.execution_mode or "react",
        "model_tier": skill.model_tier or "mid",
        "allowed_tools": skill.allowed_tools or [],
        "trigger_keywords": skill.trigger_keywords or [],
        "resources": skill.resources or {},
        "scope": skill.scope or "private",
        "is_builtin": skill.is_builtin,
        "enabled": skill.enabled,
        "is_own": False,
    }


class UserSkillCreateRequest(BaseModel):
    """创建 skill 元数据。skill 内容（system prompt / scripts / references）走 SG-5 bundle 上传 API 挂载。"""
    name: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=200)
    description: str = ""
    category: str = "general"
    model_tier: str = "mid"
    allowed_tools: list[str] = []
    trigger_keywords: list[str] = []
    resources: dict = {}


class UserSkillUpdateRequest(BaseModel):
    display_name: str | None = None
    description: str | None = None
    category: str | None = None
    model_tier: str | None = None
    allowed_tools: list[str] | None = None
    trigger_keywords: list[str] | None = None
    resources: dict | None = None


@router.get("")
async def list_available_skills(
    request: Request,
    category: str | None = None,
    page: int = 1,
    size: int = 50,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """用户可用的 Skill 列表（system + public + 自己的 private，角色白名单过滤）。"""
    svc = SkillService(db, redis_client=_get_redis(request))
    user_role_codes = [r.code for r in (user.roles or [])]
    skills, total = svc.list_skills(
        user_id=user.user_id,
        user_roles=user_role_codes,
        category=category,
        page=page,
        size=size,
    )
    result = []
    for s in skills:
        d = _skill_to_dict(s)
        d["is_own"] = s.created_by == user.user_id
        result.append(d)
    return {"skills": result, "total": total, "page": page, "size": size}


@router.post("")
async def create_private_skill(
    req: UserSkillCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """创建私有 Skill（scope=private, execution_mode=react）。"""
    svc = SkillService(db, redis_client=_get_redis(request))

    data = req.model_dump()
    data["scope"] = "private"
    data["execution_mode"] = "react"
    try:
        skill = svc.create_skill(data, created_by=user.user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    d = _skill_to_dict(skill)
    d["is_own"] = True
    return d


@router.put("/{skill_id}")
async def update_own_skill(
    skill_id: int,
    req: UserSkillUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """编辑自己的私有 Skill。"""
    svc = SkillService(db, redis_client=_get_redis(request))
    skill = svc.get_skill(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill 不存在")
    if skill.created_by != user.user_id:
        raise HTTPException(status_code=403, detail="只能编辑自己创建的 Skill")

    data = req.model_dump(exclude_none=True)
    skill = svc.update_skill(skill_id, data)
    db.commit()
    d = _skill_to_dict(skill)
    d["is_own"] = True
    return d


@router.delete("/{skill_id}")
async def delete_own_skill(
    skill_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """删除自己的私有 Skill。"""
    svc = SkillService(db, redis_client=_get_redis(request))
    skill = svc.get_skill(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill 不存在")
    if skill.created_by != user.user_id:
        raise HTTPException(status_code=403, detail="只能删除自己创建的 Skill")
    try:
        svc.delete_skill(skill_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return {"message": "已删除"}


@router.get("/tools")
async def list_available_tools(
    request: Request,
    _user: User = Depends(get_current_user),
):
    """可选 Tool 列表（创建 Skill 时选择允许的 Tool）。"""
    registry = request.app.state.registry

    tools = []
    seen = set()
    for name, app_instance in registry.apps.items():
        for tool in app_instance.skills:
            if tool.name not in seen:
                seen.add(tool.name)
                tools.append({"name": tool.name, "description": tool.description or ""})

    from lea_graph.tools import get_platform_tools
    for tool in get_platform_tools():
        if tool.name not in seen:
            seen.add(tool.name)
            tools.append({"name": tool.name, "description": tool.description or ""})

    return {"tools": tools}
