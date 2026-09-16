"""Redis 缓存 + 权限/Capabilities 快速路径（Redis 不可用时降级为无缓存）。"""

from __future__ import annotations

import json
import hashlib
import random

import structlog
import redis.asyncio as aioredis

from lea_tools.config import get_redis_settings

log = structlog.get_logger()

_pool: aioredis.Redis | None = None
_redis_available: bool = True

# 缓存 TTL 配置
CACHE_TTL_CAPABILITIES = 300  # 5分钟 - 工具清单（对齐 lea-config/lea-rag）
CACHE_TTL_NULL = 60           # 1分钟 - 空值缓存（防穿透）


def _ttl_with_jitter(base_ttl: int) -> int:
    """给 TTL 加随机抖动（±10%），防止缓存雪崩。"""
    jitter = int(base_ttl * 0.1)
    return base_ttl + random.randint(-jitter, jitter)


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


# 递增并保证有 TTL，原子。见 check_rate_limit 的说明。
#
# 用 `if ttl == -1` 而不是 `if count == 1`：后者假设"计数为 1 就是新键"，但一个**已经
# 存在却没有 TTL** 的键（正是旧代码那个竞态留下的坏键）永远不会再等于 1，于是永远补不上
# 过期时间。按 TTL 判则能自动修好那些键。
_INCR_WITH_TTL = """
local count = redis.call('INCR', KEYS[1])
if redis.call('TTL', KEYS[1]) == -1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""


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
            # 检查空值标记（防穿透）
            if data == "NULL":
                log.debug("capabilities cache hit (null marker)", tenant_id=tenant_id, user_id=user_id)
                return []
            log.debug("capabilities cache hit", tenant_id=tenant_id, user_id=user_id, role=role)
            return json.loads(data)
        else:
            log.debug("capabilities cache miss", tenant_id=tenant_id, user_id=user_id, role=role)
            return None
    except Exception as e:
        log.warning("capabilities cache read failed", error=str(e))
        _mark_unavailable()
    return None


async def set_cached_capabilities(
    tenant_id: str,
    user_id: str | None,
    role: str,
    agent_id: int | None,
    tools: list[dict],
    ttl: int = CACHE_TTL_CAPABILITIES,
):
    """缓存工具清单。

    Args:
        tenant_id: 租户 ID
        user_id: 用户 ID
        role: 角色
        agent_id: Agent ID
        tools: 工具列表，空列表时写入空值标记（防穿透）
        ttl: 过期时间（秒），默认 300 秒
    """
    r = get_redis()
    if not r:
        return
    try:
        key = _cache_key("cap", t=tenant_id, u=user_id, r=role, a=agent_id)
        # 空值使用特殊标记 + 短 TTL（防穿透）
        if not tools:
            await r.setex(key, CACHE_TTL_NULL, "NULL")
            log.debug("capabilities cache set (null marker)", tenant_id=tenant_id, user_id=user_id, ttl=CACHE_TTL_NULL)
        else:
            # 正常值加抖动 TTL（防雪崩）
            actual_ttl = _ttl_with_jitter(ttl)
            await r.setex(key, actual_ttl, json.dumps(tools, ensure_ascii=False))
            log.debug("capabilities cache set", tenant_id=tenant_id, user_id=user_id, role=role, tool_count=len(tools), ttl=actual_ttl)
    except Exception as e:
        log.warning("capabilities cache write failed", error=str(e))
        _mark_unavailable()


async def invalidate_capabilities(tenant_id: str):
    """工具变更时清除该租户的所有 capabilities 缓存。

    删除该租户下所有用户/角色/agent 组合的工具清单缓存。

    ## 为什么是全删而不是按租户过滤

    缓存键格式是 `lea-tools:cap:{hash}`，hash 包含 `(tenant_id, user_id, role, agent_id)`。
    MD5 不可逆，无法从键反推租户 —— 只能 scan 所有 `cap:*` 然后读值判断，或者全删。

    这里选全删：
    - 工具 CRUD 是低频操作（管理员配置），清空所有租户的缓存代价可接受
    - 避免逐个读值的 O(n) 网络往返（大租户可能有几百个 (user, role, agent) 组合）
    - TTL 只有 5 分钟，清空后自然重建

    如果未来工具变更变高频（比如用户频繁改工具），改成在键里明文带 tenant_id 前缀。

    Args:
        tenant_id: 租户 ID（当前实现会清除所有租户，参数保留以便未来优化）
    """
    r = get_redis()
    if not r:
        return
    try:
        deleted = 0
        pattern = "lea-tools:cap:*"
        async for key in r.scan_iter(match=pattern, count=100):
            await r.delete(key)
            deleted += 1
        if deleted > 0:
            log.info("capabilities cache invalidated", tenant_id=tenant_id, deleted=deleted)
    except Exception as e:
        log.warning("capabilities cache invalidation failed", tenant_id=tenant_id, error=str(e))
        _mark_unavailable()


async def check_rate_limit(
    tenant_id: str, tool_name: str, limit: int
) -> bool:
    """固定窗口限流。返回 True 放行。

    ## 为什么用 Lua 而不是 INCR + EXPIRE 两步

    原来是 `count = INCR(key)` 然后 `if count == 1: EXPIRE(key, 60)`。这两步之间进程
    挂掉（部署、OOM、被 kill）的话，键**永不过期** —— 计数只增不减，那个租户的这个工具
    从此永久 429。

    这比"不限流"糟得多：限流器 fail-closed 成永久拒绝，而且没有任何日志说明原因，
    只能靠人去 Redis 里手动 DEL。窗口是 60 秒，所以这个竞态窗口虽然窄，但每个 key
    的第一次调用都会经过它 —— 部署频繁时撞上只是时间问题。

    Lua 脚本在 Redis 里原子执行，INCR 和 EXPIRE 之间不存在"中间状态"。
    """
    if limit <= 0:
        return True
    r = get_redis()
    if not r:
        return True
    try:
        key = f"lea-tools:rate:{tenant_id}:{tool_name}"
        count = await r.eval(_INCR_WITH_TTL, 1, key, "60")
        return int(count) <= limit
    except Exception:
        _mark_unavailable()
        return True
