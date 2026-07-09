"""Admin Agent 绑定管理 API（Ph1 Skill v2 · Sprint A · A5）。

端点：
- Skill 绑定 4 端点：GET/POST/PUT/DELETE  /admin/agents/{agent_id}/skills
- KB 绑定    4 端点：GET/POST/PUT/DELETE  /admin/agents/{agent_id}/kbs
- Tool 绑定  4 端点：GET/POST/PUT/DELETE  /admin/agents/{agent_id}/tools
- 校验端点   1 端点：POST /admin/agents/{agent_id}/skills/{skill_id}/validate

约定：
- `required_tools` 是 skill 侧**依赖声明**，validate 端点比对当前 tool 池确认可满足
- KB 绑定既是权限白名单，又是 tool_call 默认参数源
- Tool 绑定是 agent 直接授权（与 skill 继承 union 组成 effective_tools）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from lea_server.core.deps import admin_required
from lea_server.database import get_db
from lea_server.models.agent_binding import KB_STRATEGY_VALUES
from lea_server.models.models import User
from lea_server.services.agent_binding_service import AgentBindingService

router = APIRouter(prefix="/admin/agents", tags=["admin-agent-bindings"])

_require_admin = admin_required


# ── Pydantic 请求体 ──────────────────────────────────────────


class SkillBindRequest(BaseModel):
    skill_id: int
    priority: int = 100
    enabled: bool = True


class SkillBindItem(BaseModel):
    skill_id: int
    priority: int = 100
    enabled: bool = True


class KbBindRequest(BaseModel):
    kb_id: int
    strategy: str = Field(default="hybrid")
    top_k: int = Field(default=5, ge=1, le=50)
    weight: float = Field(default=1.0, ge=0.0)
    enabled: bool = True


class KbBindItem(BaseModel):
    kb_id: int
    strategy: str = "hybrid"
    top_k: int = Field(default=5, ge=1, le=50)
    weight: float = Field(default=1.0, ge=0.0)
    enabled: bool = True


class ToolBindRequest(BaseModel):
    tool_id: int
    enabled: bool = True


class ToolBindItem(BaseModel):
    tool_id: int
    enabled: bool = True


# ── Skill Bindings ──────────────────────────────────────────


@router.get("/{agent_id}/skills")
async def list_agent_skills(
    agent_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """列出 agent 挂载的所有 skill（含 skill 详情、按 priority 升序）。"""
    svc = AgentBindingService(db)
    try:
        svc.get_agent_or_404(agent_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"items": svc.list_skill_bindings(agent_id)}


@router.post("/{agent_id}/skills")
async def add_agent_skill(
    agent_id: int,
    req: SkillBindRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """挂载单个 skill 到 agent（幂等：已存在则更新 priority/enabled）。"""
    svc = AgentBindingService(db)
    try:
        binding = svc.upsert_skill_binding(
            agent_id, req.skill_id, priority=req.priority, enabled=req.enabled
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return {
        "binding_id": binding.id,
        "agent_id": binding.agent_id,
        "skill_id": binding.skill_id,
        "priority": binding.priority,
        "enabled": binding.enabled,
    }


@router.put("/{agent_id}/skills")
async def replace_agent_skills(
    agent_id: int,
    items: list[SkillBindItem],
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """全量替换 agent 的 skill 绑定列表（原绑定被清空）。"""
    svc = AgentBindingService(db)
    try:
        result = svc.replace_skill_bindings(
            agent_id, [item.model_dump() for item in items]
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return {"items": result, "total": len(result)}


@router.delete("/{agent_id}/skills/{skill_id}")
async def remove_agent_skill(
    agent_id: int,
    skill_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """从 agent 卸载单个 skill。"""
    svc = AgentBindingService(db)
    try:
        svc.get_agent_or_404(agent_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    removed = svc.remove_skill_binding(agent_id, skill_id)
    db.commit()
    return {"removed": removed}


@router.post("/{agent_id}/skills/{skill_id}/validate")
async def validate_agent_skill(
    agent_id: int,
    skill_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """挂载前预检：required_tools 是否被当前 tool 池满足。"""
    available = _collect_available_tool_names(request)
    svc = AgentBindingService(db)
    try:
        return svc.validate_skill_binding(agent_id, skill_id, available)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── KB Bindings ─────────────────────────────────────────────


@router.get("/{agent_id}/kbs")
async def list_agent_kbs(
    agent_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """列出 agent 挂载的知识库。"""
    svc = AgentBindingService(db)
    try:
        svc.get_agent_or_404(agent_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"items": svc.list_kb_bindings(agent_id)}


@router.post("/{agent_id}/kbs")
async def add_agent_kb(
    agent_id: int,
    req: KbBindRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """挂载单个 KB 到 agent（幂等）。"""
    if req.strategy not in KB_STRATEGY_VALUES:
        raise HTTPException(
            status_code=400,
            detail=f"strategy 必须为 {sorted(KB_STRATEGY_VALUES)} 之一",
        )
    svc = AgentBindingService(db)
    try:
        binding = svc.upsert_kb_binding(
            agent_id,
            req.kb_id,
            strategy=req.strategy,
            top_k=req.top_k,
            weight=req.weight,
            enabled=req.enabled,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return {
        "binding_id": binding.id,
        "agent_id": binding.agent_id,
        "kb_id": binding.kb_id,
        "strategy": binding.strategy,
        "top_k": binding.top_k,
        "weight": binding.weight,
        "enabled": binding.enabled,
    }


@router.put("/{agent_id}/kbs")
async def replace_agent_kbs(
    agent_id: int,
    items: list[KbBindItem],
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """全量替换 agent 的 KB 绑定列表。"""
    svc = AgentBindingService(db)
    try:
        result = svc.replace_kb_bindings(
            agent_id, [item.model_dump() for item in items]
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return {"items": result, "total": len(result)}


@router.delete("/{agent_id}/kbs/{kb_id}")
async def remove_agent_kb(
    agent_id: int,
    kb_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """从 agent 卸载单个 KB。"""
    svc = AgentBindingService(db)
    try:
        svc.get_agent_or_404(agent_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    removed = svc.remove_kb_binding(agent_id, kb_id)
    db.commit()
    return {"removed": removed}


# ── Tool Bindings ───────────────────────────────────────────


@router.get("/{agent_id}/tools")
async def list_agent_tools(
    agent_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """列出 agent 直接绑的 tool。"""
    svc = AgentBindingService(db)
    try:
        svc.get_agent_or_404(agent_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"items": svc.list_tool_bindings(agent_id)}


@router.post("/{agent_id}/tools")
async def add_agent_tool(
    agent_id: int,
    req: ToolBindRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """挂载单个 tool 到 agent（幂等）。"""
    svc = AgentBindingService(db)
    try:
        binding = svc.upsert_tool_binding(agent_id, req.tool_id, enabled=req.enabled)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return {
        "binding_id": binding.id,
        "agent_id": binding.agent_id,
        "tool_id": binding.tool_id,
        "enabled": binding.enabled,
    }


@router.put("/{agent_id}/tools")
async def replace_agent_tools(
    agent_id: int,
    items: list[ToolBindItem],
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """全量替换 agent 的 tool 绑定列表。"""
    svc = AgentBindingService(db)
    try:
        result = svc.replace_tool_bindings(
            agent_id, [item.model_dump() for item in items]
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return {"items": result, "total": len(result)}


@router.delete("/{agent_id}/tools/{tool_id}")
async def remove_agent_tool(
    agent_id: int,
    tool_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    svc = AgentBindingService(db)
    try:
        svc.get_agent_or_404(agent_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    removed = svc.remove_tool_binding(agent_id, tool_id)
    db.commit()
    return {"removed": removed}


# ── Helpers ─────────────────────────────────────────────────


def _collect_available_tool_names(request: Request) -> set[str]:
    """当前进程能给 agent 用的所有 tool 名 —— platform + app + mcp。"""
    from lea_graph.mcp.client import get_mcp_client
    from lea_graph.tools import get_platform_tools

    names: set[str] = set()

    registry = getattr(request.app.state, "registry", None)
    if registry is not None:
        for app_instance in registry.apps.values():
            for tool in app_instance.skills:
                names.add(tool.name)

    for tool in get_platform_tools():
        names.add(tool.name)

    mcp = get_mcp_client()
    if mcp:
        for tool in mcp.get_tools():
            names.add(tool.name)

    return names
