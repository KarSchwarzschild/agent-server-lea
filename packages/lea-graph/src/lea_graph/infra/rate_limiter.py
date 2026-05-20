"""请求限流 — Redis令牌桶（骨架实现）。"""

from __future__ import annotations

import time

import structlog

logger = structlog.get_logger()


class RateLimiter:
    """基于Redis的令牌桶限流器。

    支持 RPM（每分钟请求数）和 daily token（每日token配额）两种限制。
    Redis不可用时退化为不限流（fail-open）。
    """

    def __init__(self, redis_client=None, rpm: int = 20, daily_tokens: int = 100_000):
        self._redis = redis_client
        self._rpm = rpm
        self._daily_tokens = daily_tokens

    async def check_rpm(self, user_id: str) -> bool:
        """检查用户是否超过RPM限制。返回True=允许, False=被限流。"""
        if self._redis is None:
            return True

        key = f"lea:ratelimit:rpm:{user_id}"
        try:
            current = await self._redis.incr(key)
            if current == 1:
                await self._redis.expire(key, 60)
            return current <= self._rpm
        except Exception:
            return True

    async def check_daily_tokens(self, user_id: str) -> bool:
        """检查用户是否超过日token配额。"""
        if self._redis is None:
            return True

        key = f"lea:ratelimit:tokens:{user_id}:{time.strftime('%Y%m%d')}"
        try:
            current = await self._redis.get(key)
            return int(current or 0) < self._daily_tokens
        except Exception:
            return True

    async def consume_tokens(self, user_id: str, tokens: int) -> None:
        """消费token配额。"""
        if self._redis is None:
            return

        key = f"lea:ratelimit:tokens:{user_id}:{time.strftime('%Y%m%d')}"
        try:
            await self._redis.incrby(key, tokens)
            await self._redis.expire(key, 86400)
        except Exception:
            pass
