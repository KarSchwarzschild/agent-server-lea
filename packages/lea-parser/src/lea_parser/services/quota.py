"""配额检查 + Redis 限流 + DB 用量记录。"""

from __future__ import annotations

import structlog
from sqlalchemy.orm import Session

from lea_parser.models.quota import TenantQuota, UserQuota
from lea_parser.models.usage import UsageRecord
from lea_parser.services import _get_redis

logger = structlog.get_logger()


class QuotaError(Exception):
    def __init__(self, message: str, code: str = "quota_exceeded"):
        self.message = message
        self.code = code
        super().__init__(message)


def check_rate_limit(tenant_id: str, user_id: str, db: Session) -> None:
    """Redis 滑动窗口限流（per hour）。超限抛 QuotaError。"""
    r = _get_redis()
    if not r:
        return

    tenant_quota = db.query(TenantQuota).filter_by(tenant_id=tenant_id).first()
    tenant_max = tenant_quota.max_calls_per_hour if tenant_quota else 500

    user_quota = db.query(UserQuota).filter_by(
        tenant_id=tenant_id, user_id=user_id
    ).first()
    user_max = user_quota.max_calls_per_hour if user_quota else 100

    tenant_key = f"parser:rate:{tenant_id}"
    tenant_count = r.incr(tenant_key)
    if tenant_count == 1:
        r.expire(tenant_key, 3600)
    if tenant_count > tenant_max:
        raise QuotaError(
            f"Tenant {tenant_id} exceeded {tenant_max} calls/hour",
            code="tenant_rate_limit",
        )

    user_key = f"parser:rate:{tenant_id}:{user_id}"
    user_count = r.incr(user_key)
    if user_count == 1:
        r.expire(user_key, 3600)
    if user_count > user_max:
        raise QuotaError(
            f"User {user_id} exceeded {user_max} calls/hour",
            code="user_rate_limit",
        )


def check_file_size(tenant_id: str, user_id: str, file_size: int, db: Session) -> None:
    """检查文件大小是否超过配额。"""
    size_mb = file_size / (1024 * 1024)

    tenant_quota = db.query(TenantQuota).filter_by(tenant_id=tenant_id).first()
    tenant_max_mb = tenant_quota.max_file_size_mb if tenant_quota else 200
    if size_mb > tenant_max_mb:
        raise QuotaError(
            f"File size {size_mb:.1f}MB exceeds tenant limit {tenant_max_mb}MB",
            code="file_too_large",
        )

    user_quota = db.query(UserQuota).filter_by(
        tenant_id=tenant_id, user_id=user_id
    ).first()
    user_max_mb = user_quota.max_file_size_mb if user_quota else 50
    if size_mb > user_max_mb:
        raise QuotaError(
            f"File size {size_mb:.1f}MB exceeds user limit {user_max_mb}MB",
            code="file_too_large",
        )


def record_usage(
    db: Session,
    tenant_id: str,
    user_id: str,
    task_id: str,
    filename: str,
    file_format: str,
    file_size: int,
    chunks_produced: int,
    duration_ms: int,
) -> UsageRecord:
    """记录一次解析调用的用量。"""
    cost = _calculate_cost(file_format, file_size, chunks_produced)
    record = UsageRecord(
        tenant_id=tenant_id,
        user_id=user_id,
        task_id=task_id,
        filename=filename,
        file_format=file_format,
        file_size=file_size,
        chunks_produced=chunks_produced,
        duration_ms=duration_ms,
        cost_units=cost,
    )
    db.add(record)
    db.commit()
    return record


def _calculate_cost(file_format: str, file_size: int, chunks: int) -> float:
    """按格式和大小计算费用单位（简单线性模型）。"""
    base = file_size / (1024 * 1024) * 0.1
    if file_format in ("pdf", "pptx"):
        base *= 2.0
    if file_format == "video":
        base *= 5.0
    return round(base, 4)
