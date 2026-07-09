"""Admin Agent 管理 API（Sprint A · A3）。

端点：
- GET    /admin/agents                  列表（enabled/is_system/search + 分页）
- GET    /admin/agents/{id}             详情（含 effective_tools + skill_count/kb_count）
- POST   /admin/agents                  创建普通 agent（is_system 强制 False）
- PUT    /admin/agents/{id}             改元数据（is_system 只允许改 description/enabled）
- PATCH  /admin/agents/{id}/enable      快捷启停
- DELETE /admin/agents/{id}             删除（is_system 拒绝）

绑定管理（skill/KB）见 admin_agent_bindings.py。
A5 会补 tool 直接绑定 CRUD 并把 effective_tools 补全。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from lea_server.core.deps import admin_required
from lea_server.core.tenant import DEFAULT_TENANT_ID, get_tenant
from lea_server.database import get_db
from lea_server.models.models import User
from lea_server.services.agent_service import AgentService

router = APIRouter(prefix="/admin/agents", tags=["admin-agents"])

_require_admin = admin_required


# ── Pydantic 请求体 ──────────────────────────────────────────


class AgentCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    enabled: bool = True
    avatar: str = ""
    welcome_message: str = ""
    soul_prompt: str = ""
    category: str = "general"
    example_questions: list[str] = []
    sort_order: int = 0


class AgentUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    avatar: str | None = None
    welcome_message: str | None = None
    soul_prompt: str | None = None
    category: str | None = None
    example_questions: list[str] | None = None
    sort_order: int | None = None


class AgentEnableRequest(BaseModel):
    enabled: bool


# ── Endpoints ────────────────────────────────────────────────


@router.get("")
async def list_agents(
    enabled: bool | None = Query(None),
    is_system: bool | None = Query(None),
    search: str | None = Query(None, description="模糊匹配 name / description"),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    svc = AgentService(db)
    rows, total = svc.list_agents(
        enabled=enabled, is_system=is_system, search=search, page=page, size=size,
    )
    return {
        "items": [AgentService.to_dict(a) for a in rows],
        "total": total,
        "page": page,
        "size": size,
    }


@router.get("/{agent_id}")
async def get_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    svc = AgentService(db)
    try:
        agent = svc.get_or_404(agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    breakdown = svc.compute_effective_tools(agent_id)
    extras = {
        "effective_tools": breakdown["tools"],
        "tools_breakdown": {
            "from_skills": breakdown["from_skills"],
            "from_direct": breakdown["from_direct"],
            "mcp_placeholders": breakdown["mcp_placeholders"],
        },
        **svc.count_bindings(agent_id),
    }
    return AgentService.to_dict(agent, extras=extras)


@router.post("")
async def create_agent(
    payload: AgentCreateRequest,
    admin: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    tenant_id = get_tenant().tenant_id or DEFAULT_TENANT_ID
    svc = AgentService(db)
    try:
        agent = svc.create(
            tenant_id=tenant_id,
            name=payload.name,
            description=payload.description,
            enabled=payload.enabled,
            created_by=getattr(admin, "username", None) or getattr(admin, "user_id", None),
            avatar=payload.avatar,
            welcome_message=payload.welcome_message,
            soul_prompt=payload.soul_prompt,
            category=payload.category,
            example_questions=payload.example_questions,
            sort_order=payload.sort_order,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return AgentService.to_dict(agent)


@router.put("/{agent_id}")
async def update_agent(
    agent_id: int,
    payload: AgentUpdateRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    svc = AgentService(db)
    data = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not data:
        # 空 payload 直接返回当前状态，别浪费 UPDATE
        try:
            return AgentService.to_dict(svc.get_or_404(agent_id))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
    try:
        agent = svc.update(agent_id, data)
    except ValueError as exc:
        # 名字冲突 / 白名单越界 → 400；不存在 → 404
        msg = str(exc)
        code = 404 if "not found" in msg else 400
        raise HTTPException(status_code=code, detail=msg)
    return AgentService.to_dict(agent)


@router.patch("/{agent_id}/enable")
async def toggle_enable(
    agent_id: int,
    payload: AgentEnableRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    svc = AgentService(db)
    try:
        agent = svc.set_enabled(agent_id, payload.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return AgentService.to_dict(agent)


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    svc = AgentService(db)
    try:
        svc.delete(agent_id)
    except ValueError as exc:
        msg = str(exc)
        code = 404 if "not found" in msg else 400
        raise HTTPException(status_code=code, detail=msg)
    return {"ok": True}
