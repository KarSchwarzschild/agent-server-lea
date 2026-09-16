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


# 递增并保证有 TTL，原子执行。
#
# 原来是 `count = INCR(key)` 然后 `if count == 1: EXPIRE(key, 3600)`。两步之间进程挂掉
# 的话键**永不过期**：计数只增不减，那个用户/租户从此永久 429，只能靠人手动 DEL。
# 限流器 fail-closed 成永久拒绝比不限流糟得多，而且窗口是一小时，坏了很难自愈。
#
# 用 `TTL == -1` 判而不是 `count == 1`：后者对**已经存在却没有 TTL** 的键（正是旧代码
# 留下的坏键）永远不成立，于是永远补不上过期时间。按 TTL 判能顺手把历史坏键修好。
_INCR_WITH_TTL = """
local count = redis.call('INCR', KEYS[1])
if redis.call('TTL', KEYS[1]) == -1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""


def _incr_window(r, key: str, ttl_seconds: int) -> int:
    """递增一个固定窗口计数器，返回当前值。"""
    return int(r.eval(_INCR_WITH_TTL, 1, key, str(ttl_seconds)))


def check_rate_limit(tenant_id: str, user_id: str, db: Session) -> None:
    """Redis 固定窗口限流（per hour）。超限抛 QuotaError。"""
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
    if _incr_window(r, tenant_key, 3600) > tenant_max:
        raise QuotaError(
            f"Tenant {tenant_id} exceeded {tenant_max} calls/hour",
            code="tenant_rate_limit",
        )

    user_key = f"parser:rate:{tenant_id}:{user_id}"
    if _incr_window(r, user_key, 3600) > user_max:
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
