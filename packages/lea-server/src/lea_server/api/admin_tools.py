"""Admin Tool 管理 API（Sprint A · Step 1）。

端点：
- GET    /admin/tools                     列表（过滤 source/category/enabled/search + 分页）
- GET    /admin/tools/{id}                详情
- POST   /admin/tools                     手工建 api 类型 tool（source=api）
- PUT    /admin/tools/{id}                改元数据（内置 tool 也允许改 display_name/category/description/is_enabled）
- PATCH  /admin/tools/{id}/enable         快捷切换 is_enabled
- DELETE /admin/tools/{id}                内置 tool 禁止删除
- POST   /admin/tools/sync-builtin        同步内置 tool（seed 对齐 lea_graph.tools registry）

设计约定：
- Tool 表是「管理层元数据」权威源，运行时 registry 的 BaseTool 实例仍在代码里。
- 内置 tool（source=builtin）通过 sync-builtin 从 registry 同步进表。
- MCP tool（source=mcp）由 mcp_servers 配置驱动，Sprint E 激活。
- API tool（source=api）由 admin 手工建，未来接 HTTP endpoint。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from lea_server.core.deps import admin_required
from lea_server.core.tenant import DEFAULT_TENANT_ID, get_tenant
from lea_server.database import get_db
from lea_server.models.models import User
from lea_server.models.tool import TOOL_SOURCE_API, TOOL_SOURCE_VALUES
from lea_server.services.tool_service import ToolService

router = APIRouter(prefix="/admin/tools", tags=["admin-tools"])

_require_admin = admin_required


# ── Pydantic 请求体 ──────────────────────────────────────────


class ToolCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=200)
    description: str = ""
    category: str = "general"
    source: str = TOOL_SOURCE_API  # 只允许 api（builtin 走 sync）
    source_config: dict = Field(default_factory=dict)
    input_schema: dict = Field(default_factory=dict)
    is_enabled: bool = True


class ToolUpdateRequest(BaseModel):
    display_name: str | None = None
    description: str | None = None
    category: str | None = None
    is_enabled: bool | None = None


class ToolEnableRequest(BaseModel):
    enabled: bool


# ── Endpoints ────────────────────────────────────────────────


@router.get("")
async def list_tools(
    source: str | None = Query(None, description=f"过滤 source：{TOOL_SOURCE_VALUES}"),
    category: str | None = Query(None),
    enabled: bool | None = Query(None),
    search: str | None = Query(None, description="模糊匹配 name / display_name"),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    svc = ToolService(db)
    rows, total = svc.list_tools(
        source=source, category=category, enabled=enabled, search=search, page=page, size=size,
    )
    return {
        "items": [ToolService.to_dict(t) for t in rows],
        "total": total,
        "page": page,
        "size": size,
    }


@router.get("/{tool_id}")
async def get_tool(
    tool_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    svc = ToolService(db)
    try:
        tool = svc.get_or_404(tool_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return ToolService.to_dict(tool)


@router.post("")
async def create_tool(
    payload: ToolCreateRequest,
    admin: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    if payload.source not in TOOL_SOURCE_VALUES:
        raise HTTPException(status_code=400, detail=f"invalid source: {payload.source}")
    if payload.source == "builtin":
        raise HTTPException(status_code=400, detail="use POST /admin/tools/sync-builtin to seed builtin tools")

    svc = ToolService(db)
    # 多租户：CREATE 用 request context 的租户（非硬编码 default）
    # 全局自动过滤器未激活 issue → 见 task #49 · 渐进补齐
    tenant_id = get_tenant().tenant_id or DEFAULT_TENANT_ID
    try:
        tool = svc.create(
            tenant_id=tenant_id,
            payload=payload.model_dump(),
            created_by=admin.username if hasattr(admin, "username") else str(admin.id),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ToolService.to_dict(tool)


@router.put("/{tool_id}")
async def update_tool(
    tool_id: int,
    payload: ToolUpdateRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    svc = ToolService(db)
    try:
        tool = svc.update(tool_id, {k: v for k, v in payload.model_dump().items() if v is not None})
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return ToolService.to_dict(tool)


@router.patch("/{tool_id}/enable")
async def toggle_enable(
    tool_id: int,
    payload: ToolEnableRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    svc = ToolService(db)
    try:
        tool = svc.set_enabled(tool_id, payload.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return ToolService.to_dict(tool)


@router.delete("/{tool_id}")
async def delete_tool(
    tool_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    svc = ToolService(db)
    try:
        svc.delete(tool_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.post("/sync-builtin")
async def sync_builtin_tools(
    request: Request,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """从 lea_graph.tools 拉取所有内置 BaseTool 元数据，seed 到 tool 表。

    多次调用幂等：已存在的 name 仅刷新 description / input_schema（保留 admin 改过的字段）。
    """
    from lea_graph.tools import get_platform_tools

    entries = []
    for tool in get_platform_tools():
        try:
            schema = {}
            if hasattr(tool, "args_schema") and tool.args_schema:
                schema = tool.args_schema.model_json_schema()
        except Exception:
            schema = {}
        entries.append({
            "name": tool.name,
            "display_name": getattr(tool, "display_name", None) or tool.name,
            "description": tool.description or "",
            "category": getattr(tool, "category", "general"),
            "input_schema": schema,
        })

    svc = ToolService(db)
    return svc.sync_builtin_tools(tenant_id=DEFAULT_TENANT_ID, registry_tools=entries)
