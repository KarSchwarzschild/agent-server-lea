"""
多租户框架 — 请求级租户隔离。

核心机制：
1. TenantContext（ContextVar）：async-safe 的请求级租户上下文
2. TenantMiddleware：从 JWT / API Key 自动解析 tenant_id 并注入上下文
3. SQLAlchemy event hook：查询自动追加 WHERE tenant_id = ?
4. TenantConfig 模型：租户级配置（LLM模型、KB_ID、配额等）
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Column, DateTime, Integer, String, event, func
from sqlalchemy.orm import Session

from lea_server.database import Base

_tenant_ctx_var: ContextVar[TenantContext | None] = ContextVar("tenant_context", default=None)

DEFAULT_TENANT_ID = "default"


@dataclass
class TenantContext:
    """请求级租户上下文，通过 ContextVar 传递（async-safe）。"""

    tenant_id: str
    config: TenantConfig | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def get_tenant() -> TenantContext:
    ctx = _tenant_ctx_var.get()
    if ctx is None:
        return TenantContext(tenant_id=DEFAULT_TENANT_ID)
    return ctx


def set_tenant(ctx: TenantContext) -> None:
    _tenant_ctx_var.set(ctx)


def clear_tenant() -> None:
    _tenant_ctx_var.set(None)


class TenantConfig(Base):
    """租户级配置表 — 控制模型选择、知识库、配额。"""

    __tablename__ = "tenant_configs"

    tenant_id = Column(String(50), primary_key=True)
    display_name = Column(String(100), default="")
    llm_model = Column(String(50), default="qwen-max-latest")
    kb_id = Column(String(100), default="")
    rpm_limit = Column(Integer, default=20)
    daily_token_limit = Column(Integer, default=100_000)
    monthly_token_limit = Column(Integer, default=3_000_000)
    is_active = Column(Integer, default=1)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class TenantMixin:
    """混入类 — 所有需要租户隔离的业务表继承此类。

    使用方式：
        class ChatSession(TenantMixin, Base):
            __tablename__ = "chat_sessions"
            ...
    """

    tenant_id = Column(String(50), nullable=False, index=True, default=DEFAULT_TENANT_ID)


def get_tenant_config(db: Session, tenant_id: str) -> TenantConfig | None:
    return db.query(TenantConfig).filter(TenantConfig.tenant_id == tenant_id).first()


def get_or_create_tenant_config(db: Session, tenant_id: str) -> TenantConfig:
    config = get_tenant_config(db, tenant_id)
    if config is None:
        config = TenantConfig(tenant_id=tenant_id, display_name=tenant_id)
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


_TENANT_ISOLATED_TABLES: set[str] = set()


def register_tenant_table(table_name: str) -> None:
    _TENANT_ISOLATED_TABLES.add(table_name)


def enable_tenant_filter(session_factory) -> None:
    """注册 SQLAlchemy event hook，查询自动追加 tenant_id 过滤。

    对 _TENANT_ISOLATED_TABLES 中注册的表，SELECT 时自动加
    WHERE tenant_id = <current_tenant_id>。
    """

    @event.listens_for(session_factory, "do_orm_execute")
    def _add_tenant_filter(execute_state):
        if not execute_state.is_select:
            return

        ctx = _tenant_ctx_var.get()
        if ctx is None:
            return

        tenant_id = ctx.tenant_id
        if tenant_id == DEFAULT_TENANT_ID:
            return

        stmt = execute_state.statement
        for frm in stmt.froms:
            table_name = getattr(frm, "name", None)
            if table_name and table_name in _TENANT_ISOLATED_TABLES:
                execute_state.statement = stmt.where(frm.c.tenant_id == tenant_id)
                break
