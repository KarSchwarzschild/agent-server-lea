"""
请求限流 — Redis 滑动窗口 + Token 配额。

per-tenant 限流：RPM（滑动窗口）+ 日/月 Token 配额。
Redis 不可用时 fail-open（不阻塞业务）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()

_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])

redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count < limit then
    redis.call('ZADD', key, now, now .. '-' .. math.random(100000))
    redis.call('EXPIRE', key, window)
    return 1
end
return 0
"""


@dataclass
class RateLimitResult:
    allowed: bool
    remaining: int = 0
    retry_after: float = 0.0
    limit: int = 0


_instance: RateLimiter | None = None


def init_rate_limiter(redis_client) -> RateLimiter:
    global _instance
    _instance = RateLimiter(redis_client)
    return _instance


def get_rate_limiter() -> RateLimiter | None:
    return _instance


class RateLimiter:
    """per-tenant 限流器。

    RPM: Redis 滑动窗口（ZSET），精度秒级
    Token 配额: Redis INCRBY，日/月两档
    """

    def __init__(self, redis_client=None):
        self._redis = redis_client
        self._lua_sha: str | None = None

    async def _ensure_lua(self) -> str | None:
        if self._redis is None:
            return None
        if self._lua_sha is None:
            try:
                self._lua_sha = await self._redis.script_load(_SLIDING_WINDOW_LUA)
            except Exception:
                return None
        return self._lua_sha

    async def check_rpm(self, tenant_id: str, rpm_limit: int = 20) -> RateLimitResult:
        if self._redis is None:
            return RateLimitResult(allowed=True, remaining=rpm_limit, limit=rpm_limit)

        key = f"lea:rate:{tenant_id}:rpm"
        now = time.time()
        window = 60

        try:
            sha = await self._ensure_lua()
            if sha:
                result = await self._redis.evalsha(sha, 1, key, str(now), str(window), str(rpm_limit))
                allowed = result == 1
            else:
                await self._redis.zremrangebyscore(key, 0, now - window)
                count = await self._redis.zcard(key)
                allowed = count < rpm_limit
                if allowed:
                    await self._redis.zadd(key, {f"{now}-{id(key)}": now})
                    await self._redis.expire(key, window)

            if allowed:
                count = await self._redis.zcard(key)
                return RateLimitResult(
                    allowed=True,
                    remaining=max(0, rpm_limit - count),
                    limit=rpm_limit,
                )
            else:
                oldest = await self._redis.zrange(key, 0, 0, withscores=True)
                retry_after = (oldest[0][1] + window - now) if oldest else window
                return RateLimitResult(
                    allowed=False,
                    remaining=0,
                    retry_after=max(0, retry_after),
                    limit=rpm_limit,
                )
        except Exception as e:
            logger.warning("rate_limiter_error", error=str(e))
            return RateLimitResult(allowed=True, remaining=rpm_limit, limit=rpm_limit)

    async def get_rpm_status(self, tenant_id: str, rpm_limit: int = 20) -> RateLimitResult:
        """只读 RPM 查询 — 不写入 ZADD，安全用于面板轮询。"""
        if self._redis is None:
            return RateLimitResult(allowed=True, remaining=rpm_limit, limit=rpm_limit)

        key = f"lea:rate:{tenant_id}:rpm"
        try:
            await self._redis.zremrangebyscore(key, 0, time.time() - 60)
            count = await self._redis.zcard(key)
            return RateLimitResult(
                allowed=count < rpm_limit,
                remaining=max(0, rpm_limit - count),
                limit=rpm_limit,
            )
        except Exception:
            return RateLimitResult(allowed=True, remaining=rpm_limit, limit=rpm_limit)

    async def check_daily_tokens(self, tenant_id: str, daily_limit: int = 100_000) -> RateLimitResult:
        if self._redis is None:
            return RateLimitResult(allowed=True, remaining=daily_limit, limit=daily_limit)

        key = f"lea:quota:{tenant_id}:tokens:{time.strftime('%Y%m%d')}"
        try:
            current = int(await self._redis.get(key) or 0)
            remaining = max(0, daily_limit - current)
            return RateLimitResult(
                allowed=current < daily_limit,
                remaining=remaining,
                limit=daily_limit,
            )
        except Exception:
            return RateLimitResult(allowed=True, remaining=daily_limit, limit=daily_limit)

    async def consume_tokens(self, tenant_id: str, tokens: int) -> None:
        if self._redis is None:
            return

        day_key = f"lea:quota:{tenant_id}:tokens:{time.strftime('%Y%m%d')}"
        month_key = f"lea:quota:{tenant_id}:tokens:{time.strftime('%Y%m')}"
        try:
            pipe = self._redis.pipeline()
            pipe.incrby(day_key, tokens)
            pipe.expire(day_key, 86400)
            pipe.incrby(month_key, tokens)
            pipe.expire(month_key, 86400 * 31)
            await pipe.execute()
        except Exception as e:
            logger.warning("token_consume_error", error=str(e))

    async def get_usage(self, tenant_id: str) -> dict:
        if self._redis is None:
            return {"daily_tokens": 0, "monthly_tokens": 0}

        try:
            day_key = f"lea:quota:{tenant_id}:tokens:{time.strftime('%Y%m%d')}"
            month_key = f"lea:quota:{tenant_id}:tokens:{time.strftime('%Y%m')}"
            pipe = self._redis.pipeline()
            pipe.get(day_key)
            pipe.get(month_key)
            daily, monthly = await pipe.execute()
            return {"daily_tokens": int(daily or 0), "monthly_tokens": int(monthly or 0)}
        except Exception:
            return {"daily_tokens": 0, "monthly_tokens": 0}
