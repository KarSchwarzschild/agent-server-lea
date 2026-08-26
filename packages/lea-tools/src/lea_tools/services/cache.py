"""Redis 缓存 + 权限/Capabilities 快速路径（Redis 不可用时降级为无缓存）。"""

from __future__ import annotations

import json
import hashlib

import structlog
import redis.asyncio as aioredis

from lea_tools.config import get_redis_settings

log = structlog.get_logger()

_pool: aioredis.Redis | None = None
_redis_available: bool = True


def get_redis() -> aioredis.Redis | None:
    global _pool, _redis_available
    if not _redis_available:
        return None
    if _pool is None:
        settings = get_redis_settings()
        _pool = aioredis.from_url(settings.url, decode_responses=True)
    return _pool


def _cache_key(prefix: str, **kwargs) -> str:
    raw = json.dumps(kwargs, sort_keys=True)
    h = hashlib.md5(raw.encode()).hexdigest()[:12]
    return f"lea-tools:{prefix}:{h}"


def _mark_unavailable():
    global _redis_available
    if _redis_available:
        _redis_available = False
        log.warning("redis unavailable, degrading to no-cache mode")


async def get_cached_capabilities(
    tenant_id: str, user_id: str | None, role: str, agent_id: int | None
) -> list[dict] | None:
    """⚠️ `user_id` 必须进缓存键。

    工具清单从"按租户可见"变成"按人可见"之后（工具名用户内唯一，见 migrations/007），
    同租户同角色的 alice 和 bob 拿到的是**不同的**清单 —— 键里不带人就是把先算出来
    的那份发给后来的那个人，等于跨用户泄漏工具名和 schema。这个洞不是历史遗留，
    是归属改动**新产生**的，所以跟着这次改一起补。
    """
    r = get_redis()
    if not r:
        return None
    try:
        key = _cache_key("cap", t=tenant_id, u=user_id, r=role, a=agent_id)
        data = await r.get(key)
        if data:
            return json.loads(data)
    except Exception:
        _mark_unavailable()
    return None


async def set_cached_capabilities(
    tenant_id: str,
    user_id: str | None,
    role: str,
    agent_id: int | None,
    tools: list[dict],
    ttl: int = 60,
):
    r = get_redis()
    if not r:
        return
    try:
        key = _cache_key("cap", t=tenant_id, u=user_id, r=role, a=agent_id)
        await r.set(key, json.dumps(tools, ensure_ascii=False), ex=ttl)
    except Exception:
        _mark_unavailable()


async def invalidate_capabilities(tenant_id: str):
    r = get_redis()
    if not r:
        return
    try:
        pattern = "lea-tools:cap:*"
        async for key in r.scan_iter(match=pattern, count=100):
            await r.delete(key)
    except Exception:
        _mark_unavailable()


async def check_rate_limit(
    tenant_id: str, tool_name: str, limit: int
) -> bool:
    if limit <= 0:
        return True
    r = get_redis()
    if not r:
        return True
    try:
        key = f"lea-tools:rate:{tenant_id}:{tool_name}"
        count = await r.incr(key)
        if count == 1:
            await r.expire(key, 60)
        return count <= limit
    except Exception:
        _mark_unavailable()
        return True
