"""用量查询 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query
from lea_authkit import require_admin
from sqlalchemy import func
from sqlalchemy.orm import Session

from lea_parser.api.console import console_actor_from_headers
from lea_parser.auth import CurrentUser, get_current_user
from lea_parser.database import get_db
from lea_parser.models.usage import UsageRecord

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("")
def get_usage(
    days: int = Query(30, ge=1, le=365),
    user_id: str | None = None,
    authorization: str = Header(""),
    x_internal_token: str = Header("", alias="X-Internal-Token"),
    x_user_id: str = Header("", alias="X-User-ID"),
    x_tenant_id: str = Header("", alias="X-Tenant-ID"),
    x_user_role: str = Header("", alias="X-User-Role"),
    db: Session = Depends(get_db),
):
    """查询当前用户/租户的用量统计。"""
    from datetime import UTC, datetime, timedelta
    since = datetime.now(UTC) - timedelta(days=days)

    is_console_request = bool(
        x_internal_token or x_user_id or x_tenant_id or x_user_role
    )
    if is_console_request:
        actor = console_actor_from_headers(
            x_internal_token, x_user_id, x_tenant_id, x_user_role
        )
        require_admin(actor)
        tenant_id = None if actor.is_platform_admin else actor.tenant_id
        effective_user_id = user_id
    else:
        user: CurrentUser = get_current_user(authorization)
        tenant_id = user.tenant_id
        effective_user_id = None if user.role == "admin" else user.user_id

    base_q = db.query(UsageRecord).filter(
        UsageRecord.created_at >= since,
    )
    if tenant_id is not None:
        base_q = base_q.filter(UsageRecord.tenant_id == tenant_id)
    if effective_user_id:
        base_q = base_q.filter(UsageRecord.user_id == effective_user_id)

    total_calls = base_q.count()
    total_size, total_cost = base_q.with_entities(
        func.coalesce(func.sum(UsageRecord.file_size), 0),
        func.coalesce(func.sum(UsageRecord.cost_units), 0),
    ).one()

    return {
        "tenant_id": tenant_id,
        "period_days": days,
        "total_calls": total_calls,
        "total_size_bytes": int(total_size or 0),
        "total_cost_units": round(float(total_cost or 0), 4),
    }
