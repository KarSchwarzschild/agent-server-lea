"""Admin Skill/Tool 管理 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from lea_server.core.deps import admin_required
from lea_server.database import get_db
from lea_server.models.models import User
from lea_server.models.skill import Skill
from lea_server.services.skill_service import SkillService

router = APIRouter(prefix="/admin", tags=["admin-skills"])

_require_admin = admin_required


# ── Tool API（只读）──────────────────────────────────────────


@router.get("/tools")
async def list_tools(request: Request, _admin: User = Depends(_require_admin)):
    """返回所有已注册 Tool 的元信息。"""
    registry = request.app.state.registry

    tools = []
    seen = set()

    for name, app_instance in registry.apps.items():
        for tool in app_instance.skills:
            if tool.name not in seen:
                seen.add(tool.name)
                tools.append({
                    "name": tool.name,
                    "description": tool.description or "",
                    "source": f"app:{name}",
                })

    from lea_graph.tools import get_platform_tools
    for tool in get_platform_tools():
        if tool.name not in seen:
            seen.add(tool.name)
            tools.append({
                "name": tool.name,
                "description": tool.description or "",
                "source": "platform",
            })

    return {"tools": tools, "total": len(tools)}


# ── Skill CRUD API ──────────────────────────────────────────


class SkillCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=200)
    description: str = ""
    category: str = "general"
    execution_mode: str = "react"
    pipeline_class: str = ""
    context: str = "fork"
    model_tier: str = "mid"
    allowed_tools: list[str] = []
    trigger_keywords: list[str] = []
    system_prompt: str = ""
    resources: dict = {}


class SkillUpdateRequest(BaseModel):
    display_name: str | None = None
    description: str | None = None
    category: str | None = None
    execution_mode: str | None = None
    pipeline_class: str | None = None
    context: str | None = None
    model_tier: str | None = None
    allowed_tools: list[str] | None = None
    trigger_keywords: list[str] | None = None
    system_prompt: str | None = None
    resources: dict | None = None
    enabled: bool | None = None


def _skill_to_dict(skill: Skill) -> dict:
    return {
        "id": skill.id,
        "name": skill.name,
        "display_name": skill.display_name,
        "description": skill.description or "",
        "version": skill.version or "1.0.0",
        "category": skill.category or "general",
        "execution_mode": skill.execution_mode or "react",
        "pipeline_class": skill.pipeline_class or "",
        "context": skill.context or "fork",
        "model_tier": skill.model_tier or "mid",
        "allowed_tools": skill.allowed_tools or [],
        "trigger_keywords": skill.trigger_keywords or [],
        "system_prompt": skill.system_prompt or "",
        "resources": skill.resources or {},
        "scope": skill.scope or "private",
        "is_builtin": skill.is_builtin,
        "enabled": skill.enabled,
        "created_by": skill.created_by,
        "created_at": skill.created_at.isoformat() if skill.created_at else None,
        "updated_at": skill.updated_at.isoformat() if skill.updated_at else None,
    }


def _get_redis(request: Request):
    return getattr(request.app.state, "redis", None)


@router.get("/skills")
async def list_skills(
    request: Request,
    scope: str | None = None,
    category: str | None = None,
    page: int = 1,
    size: int = 50,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """管理员查看所有 Skill（不限 scope）。"""
    svc = SkillService(db, redis_client=_get_redis(request))
    skills, total = svc.list_skills(
        scope=scope, category=category,
        enabled_only=False, page=page, size=size,
    )
    return {
        "skills": [_skill_to_dict(s) for s in skills],
        "total": total,
        "page": page,
        "size": size,
    }


@router.post("/skills")
async def create_skill(
    req: SkillCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(_require_admin),
):
    """创建公共 Skill（scope=public）。"""
    svc = SkillService(db, redis_client=_get_redis(request))
    if svc.get_skill_by_name(req.name):
        raise HTTPException(status_code=400, detail=f"Skill '{req.name}' 已存在")

    data = req.model_dump()
    data["scope"] = "public"
    try:
        skill = svc.create_skill(data, created_by=admin.user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return _skill_to_dict(skill)


@router.get("/skills/{skill_id}")
async def get_skill(
    skill_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    skill = SkillService(db, redis_client=_get_redis(request)).get_skill(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill 不存在")
    return _skill_to_dict(skill)


@router.put("/skills/{skill_id}")
async def update_skill(
    skill_id: int,
    req: SkillUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    svc = SkillService(db, redis_client=_get_redis(request))
    skill = svc.get_skill(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill 不存在")

    data = req.model_dump(exclude_none=True)

    if skill.is_builtin:
        safe_fields = {"enabled", "model_tier"}
        forbidden = set(data.keys()) - safe_fields
        if forbidden:
            raise HTTPException(
                status_code=400,
                detail=f"内置 Skill 只能修改 {safe_fields}，不能修改 {forbidden}",
            )

    skill = svc.update_skill(skill_id, data)
    db.commit()
    return _skill_to_dict(skill)


@router.delete("/skills/{skill_id}")
async def delete_skill(
    skill_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    svc = SkillService(db, redis_client=_get_redis(request))
    try:
        svc.delete_skill(skill_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return {"message": "已删除"}


@router.post("/skills/sync")
async def sync_skills(
    request: Request,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """手动触发 skill.md 同步。"""
    from pathlib import Path

    from lea_graph.skills.loader import SkillLoader

    import lea_graph.skills as _skills_pkg
    builtin_dir = Path(_skills_pkg.__file__).parent / "builtin"
    loader = SkillLoader([str(builtin_dir)] if builtin_dir.is_dir() else [])

    definitions = list(loader.load_all().values())
    svc = SkillService(db, redis_client=_get_redis(request))
    count = svc.sync_builtins(definitions)
    db.commit()
    return {"message": f"同步完成，共 {count} 个内置 Skill"}
