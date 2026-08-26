"""用量查询 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from lea_parser.auth import CurrentUser, get_current_user
from lea_parser.database import get_db
from lea_parser.models.usage import UsageRecord

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("")
def get_usage(
    days: int = Query(30, ge=1, le=365),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """查询当前用户/租户的用量统计。"""
    from datetime import UTC, datetime, timedelta
    since = datetime.now(UTC) - timedelta(days=days)

    base_q = db.query(UsageRecord).filter(
        UsageRecord.tenant_id == user.tenant_id,
        UsageRecord.created_at >= since,
    )

    if user.role != "admin":
        base_q = base_q.filter(UsageRecord.user_id == user.user_id)

    total_calls = base_q.count()
    total_size = db.query(func.sum(UsageRecord.file_size)).filter(
        UsageRecord.tenant_id == user.tenant_id,
        UsageRecord.created_at >= since,
    ).scalar() or 0
    total_cost = db.query(func.sum(UsageRecord.cost_units)).filter(
        UsageRecord.tenant_id == user.tenant_id,
        UsageRecord.created_at >= since,
    ).scalar() or 0.0

    return {
        "tenant_id": user.tenant_id,
        "period_days": days,
        "total_calls": total_calls,
        "total_size_bytes": total_size,
        "total_cost_units": round(total_cost, 4),
    }
