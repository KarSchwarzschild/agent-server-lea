"""Tool 平台服务 —— CRUD + seed + registry 连线。

内置 tool 由 lea_graph/tools/ 定义 BaseTool 实例 → 启动时 seed 到表。
MCP tool 由 mcp_servers 配置驱动，本 service 提供 upsert 入口（Sprint E 激活）。
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lea_server.core.tenant import DEFAULT_TENANT_ID
from lea_server.models.tool import (
    TOOL_SOURCE_API,
    TOOL_SOURCE_BUILTIN,
    TOOL_SOURCE_MCP,
    TOOL_SOURCE_VALUES,
    Tool,
)

logger = structlog.get_logger()


# admin 可通过 PUT 修改的字段（内置 tool 也只允许改这些）
_UI_EDITABLE_COLS = {
    "display_name",
    "description",
    "category",
    "is_enabled",
}


class ToolService:
    """Tool CRUD + 生命周期。"""

    def __init__(self, db: Session):
        self.db = db

    # ── 查询 ──────────────────────────────────────────────────

    def get_or_404(self, tool_id: int) -> Tool:
        tool = self.db.query(Tool).filter(Tool.id == tool_id).first()
        if tool is None:
            raise ValueError(f"Tool id={tool_id} not found")
        return tool

    def get_by_name(self, tenant_id: str, name: str) -> Tool | None:
        return (
            self.db.query(Tool)
            .filter(Tool.tenant_id == tenant_id, Tool.name == name)
            .first()
        )

    def list_tools(
        self,
        *,
        source: str | None = None,
        category: str | None = None,
        enabled: bool | None = None,
        search: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[Tool], int]:
        q = self.db.query(Tool)
        if source:
            q = q.filter(Tool.source == source)
        if category:
            q = q.filter(Tool.category == category)
        if enabled is not None:
            q = q.filter(Tool.is_enabled == enabled)
        if search:
            like = f"%{search}%"
            q = q.filter((Tool.name.ilike(like)) | (Tool.display_name.ilike(like)))
        total = q.count()
        rows = q.order_by(Tool.category, Tool.name).offset((page - 1) * size).limit(size).all()
        return rows, total

    # ── 写入 ──────────────────────────────────────────────────

    def create(self, *, tenant_id: str, payload: dict, created_by: str | None = None) -> Tool:
        source = payload.get("source", TOOL_SOURCE_API)
        if source not in TOOL_SOURCE_VALUES:
            raise ValueError(f"invalid source: {source}")
        if source == TOOL_SOURCE_BUILTIN:
            raise ValueError("builtin tool must be seeded, not created via API")

        tool = Tool(
            tenant_id=tenant_id,
            name=payload["name"],
            display_name=payload.get("display_name") or payload["name"],
            description=payload.get("description", ""),
            category=payload.get("category", "general"),
            source=source,
            source_config=payload.get("source_config") or {},
            input_schema=payload.get("input_schema") or {},
            is_enabled=payload.get("is_enabled", True),
            is_builtin=False,
            created_by=created_by,
        )
        self.db.add(tool)
        try:
            self.db.commit()
            self.db.refresh(tool)
        except IntegrityError as exc:
            self.db.rollback()
            raise ValueError(f"tool name '{payload['name']}' already exists") from exc
        logger.info("tool_created", name=tool.name, source=tool.source)
        return tool

    def update(self, tool_id: int, payload: dict) -> Tool:
        tool = self.get_or_404(tool_id)
        for key, val in payload.items():
            if key not in _UI_EDITABLE_COLS:
                continue
            setattr(tool, key, val)
        self.db.commit()
        self.db.refresh(tool)
        logger.info("tool_updated", id=tool.id, name=tool.name)
        return tool

    def set_enabled(self, tool_id: int, enabled: bool) -> Tool:
        tool = self.get_or_404(tool_id)
        tool.is_enabled = enabled
        self.db.commit()
        self.db.refresh(tool)
        logger.info("tool_enabled_toggled", id=tool.id, enabled=enabled)
        return tool

    def delete(self, tool_id: int) -> None:
        tool = self.get_or_404(tool_id)
        if tool.is_builtin:
            raise ValueError("builtin tool cannot be deleted; disable it instead")
        self.db.delete(tool)
        self.db.commit()
        logger.info("tool_deleted", id=tool_id)

    # ── Seed / Sync ───────────────────────────────────────────

    def sync_builtin_tools(
        self,
        *,
        tenant_id: str = DEFAULT_TENANT_ID,
        registry_tools: list[dict],
    ) -> dict:
        """把 lea_graph.tools 里的内置 tool 同步到本表。

        约定：
        - registry_tools 里的每条应含 {name, description, input_schema, category?}
        - 已存在的 name：只更新 description / input_schema（保留 admin 改过的 display_name / category / is_enabled）
        - 新的：INSERT + is_builtin=True + is_enabled=True

        返回统计：{inserted, updated, total}
        """
        inserted = 0
        updated = 0
        # 多节点并发 seed 安全：INSERT 时若被 UniqueConstraint(tenant_id, name) 拦截，
        # 回滚 → 视作已存在 → 走 UPDATE 分支。
        for entry in registry_tools:
            name = entry.get("name")
            if not name:
                continue
            existing = self.get_by_name(tenant_id, name)
            if existing is None:
                self.db.add(Tool(
                    tenant_id=tenant_id,
                    name=name,
                    display_name=entry.get("display_name") or name,
                    description=entry.get("description", ""),
                    category=entry.get("category", "general"),
                    source=TOOL_SOURCE_BUILTIN,
                    source_config={},
                    input_schema=entry.get("input_schema") or {},
                    is_enabled=True,
                    is_builtin=True,
                    created_by="system",
                ))
                try:
                    self.db.commit()
                    inserted += 1
                except IntegrityError:
                    self.db.rollback()
                    # 另一节点抢先插入；改为 UPDATE 保持双方一致
                    existing = self.get_by_name(tenant_id, name)
                    if existing and existing.source == TOOL_SOURCE_BUILTIN:
                        existing.description = entry.get("description", existing.description)
                        existing.input_schema = entry.get("input_schema") or existing.input_schema
                        self.db.commit()
                        updated += 1
            else:
                # 内置来源的元数据同步（不覆盖 admin 改过的字段）
                if existing.source == TOOL_SOURCE_BUILTIN:
                    existing.description = entry.get("description", existing.description)
                    existing.input_schema = entry.get("input_schema") or existing.input_schema
                    self.db.commit()
                    updated += 1
        total = self.db.query(Tool).filter(Tool.tenant_id == tenant_id).count()
        logger.info("tool_sync_builtin", inserted=inserted, updated=updated, total=total)
        return {"inserted": inserted, "updated": updated, "total": total}

    def upsert_mcp_tool(
        self,
        *,
        tenant_id: str,
        server_id: int,
        remote_name: str,
        display_name: str,
        description: str,
        input_schema: dict,
    ) -> Tool:
        """MCP tool 反射入表（Sprint E 激活时使用）。

        Name 约定：`mcp:{server_id}:{remote_name}`，保证全局唯一。
        """
        name = f"mcp:{server_id}:{remote_name}"
        existing = self.get_by_name(tenant_id, name)
        if existing:
            existing.display_name = display_name
            existing.description = description
            existing.input_schema = input_schema
            existing.source_config = {"server_id": server_id, "remote_name": remote_name}
            self.db.commit()
            self.db.refresh(existing)
            return existing
        tool = Tool(
            tenant_id=tenant_id,
            name=name,
            display_name=display_name,
            description=description,
            category="mcp",
            source=TOOL_SOURCE_MCP,
            source_config={"server_id": server_id, "remote_name": remote_name},
            input_schema=input_schema,
            is_enabled=True,
            is_builtin=False,
            created_by="mcp-sync",
        )
        self.db.add(tool)
        self.db.commit()
        self.db.refresh(tool)
        return tool

    # ── 序列化 ────────────────────────────────────────────────

    @staticmethod
    def to_dict(tool: Tool) -> dict[str, Any]:
        return {
            "id": tool.id,
            "name": tool.name,
            "display_name": tool.display_name,
            "description": tool.description,
            "category": tool.category,
            "source": tool.source,
            "source_config": tool.source_config or {},
            "input_schema": tool.input_schema or {},
            "is_enabled": tool.is_enabled,
            "is_builtin": tool.is_builtin,
            "created_by": tool.created_by,
            "created_at": tool.created_at.isoformat() if tool.created_at else None,
            "updated_at": tool.updated_at.isoformat() if tool.updated_at else None,
        }
