"""Redis 缓存层 — Agent/Skill/Tool/MCP 配置数据缓存。

读多写少的配置数据适合缓存：
- Agent 列表和详情（用户每次打开界面都查）
- Skills/Tools/MCP 列表
- Team 配置

写操作主动失效缓存，Redis 不可用时降级到数据库直查。
"""

from __future__ import annotations

import json
import hashlib
import random
from typing import Any, Optional

import structlog
import redis.asyncio as aioredis

log = structlog.get_logger()

_pool: aioredis.Redis | None = None
_redis_available: bool = True

# 缓存 TTL 配置
CACHE_TTL_AGENT = 300  # 5分钟 - Agent 配置
CACHE_TTL_SKILL = 300  # 5分钟 - Skill 列表
CACHE_TTL_TOOL = 300   # 5分钟 - Tool 列表
CACHE_TTL_MCP = 300    # 5分钟 - MCP Server 列表
CACHE_TTL_TEAM = 300   # 5分钟 - Team 配置
CACHE_TTL_NULL = 60    # 1分钟 - 空值缓存（防穿透）


def _ttl_with_jitter(base_ttl: int) -> int:
    """给 TTL 加随机抖动（±10%），防止缓存雪崩。"""
    jitter = int(base_ttl * 0.1)
    return base_ttl + random.randint(-jitter, jitter)


def get_redis() -> aioredis.Redis | None:
    """获取 Redis 连接池（懒初始化）。"""
    global _pool, _redis_available
    if not _redis_available:
        return None
    if _pool is None:
        import os
        url = os.getenv("REDIS_URL", "")
        if not url:
            log.info("redis not configured, cache disabled")
            _redis_available = False
            return None
        try:
            _pool = aioredis.from_url(url, decode_responses=True)
        except Exception as e:
            log.warning("redis connection failed", error=str(e))
            _redis_available = False
            return None
    return _pool


def _mark_unavailable():
    """标记 Redis 不可用，后续请求直接跳过缓存。"""
    global _redis_available
    if _redis_available:
        _redis_available = False
        log.warning("redis unavailable, degrading to no-cache mode")


def _cache_key(prefix: str, **kwargs) -> str:
    """生成缓存键：lea-config:<prefix>:<hash>。"""
    raw = json.dumps(kwargs, sort_keys=True)
    h = hashlib.md5(raw.encode()).hexdigest()[:12]
    return f"lea-config:{prefix}:{h}"


async def get_cached(prefix: str, ttl: int, **kwargs) -> Optional[dict | list]:
    """从缓存读取 JSON 数据。

    Args:
        prefix: 缓存前缀（agent/skill/tool/mcp/team）
        ttl: TTL（秒），用于日志
        **kwargs: 缓存键参数（会被哈希）

    Returns:
        缓存的数据（dict/list），未命中或失败返回 None
    """
    redis = get_redis()
    if redis is None:
        return None

    key = _cache_key(prefix, **kwargs)
    try:
        value = await redis.get(key)
        if value:
            # 检查是否是空值标记（防穿透）
            if value == "NULL":
                log.debug("cache hit (null marker)", key=key, prefix=prefix)
                return None
            log.debug("cache hit", key=key, prefix=prefix)
            return json.loads(value)
        else:
            log.debug("cache miss", key=key, prefix=prefix)
            return None
    except Exception as e:
        log.warning("cache read failed", key=key, error=str(e))
        _mark_unavailable()
        return None


async def set_cached(prefix: str, ttl: int, data: dict | list | None, **kwargs):
    """写入缓存（JSON 序列化）。

    Args:
        prefix: 缓存前缀
        ttl: 过期时间（秒）
        data: 要缓存的数据，None 时写入空值标记（防穿透）
        **kwargs: 缓存键参数
    """
    redis = get_redis()
    if redis is None:
        return

    key = _cache_key(prefix, **kwargs)
    try:
        # 空值使用特殊标记 + 短 TTL（防穿透）
        if data is None or (isinstance(data, list) and len(data) == 0):
            await redis.setex(key, CACHE_TTL_NULL, "NULL")
            log.debug("cache set (null marker)", key=key, prefix=prefix, ttl=CACHE_TTL_NULL)
        else:
            # 正常值加抖动 TTL（防雪崩）
            actual_ttl = _ttl_with_jitter(ttl)
            await redis.setex(key, actual_ttl, json.dumps(data))
            log.debug("cache set", key=key, prefix=prefix, ttl=actual_ttl)
    except Exception as e:
        log.warning("cache write failed", key=key, error=str(e))
        _mark_unavailable()


async def invalidate_cached(prefix: str, **kwargs):
    """删除指定缓存。

    Args:
        prefix: 缓存前缀
        **kwargs: 缓存键参数
    """
    redis = get_redis()
    if redis is None:
        return

    key = _cache_key(prefix, **kwargs)
    try:
        await redis.delete(key)
        log.debug("cache invalidated", key=key, prefix=prefix)
    except Exception as e:
        log.warning("cache invalidation failed", key=key, error=str(e))


async def invalidate_pattern(pattern: str):
    """批量删除匹配模式的缓存键。

    Args:
        pattern: Redis key 模式，如 "lea-config:agent:*"

    注意：SCAN 在大数据集下可能慢，谨慎使用。
    """
    redis = get_redis()
    if redis is None:
        return

    try:
        deleted = 0
        async for key in redis.scan_iter(match=pattern, count=100):
            await redis.delete(key)
            deleted += 1
        if deleted > 0:
            log.info("cache pattern invalidated", pattern=pattern, deleted=deleted)
    except Exception as e:
        log.warning("cache pattern invalidation failed", pattern=pattern, error=str(e))


# ── 便捷函数：按资源类型缓存 ────────────────────────────────────────

async def get_agent_list_cache(tenant_id: str, user_id: str) -> Optional[list]:
    """获取用户可见的 Agent 列表缓存。"""
    return await get_cached("agent-list", CACHE_TTL_AGENT, tenant_id=tenant_id, user_id=user_id)


async def set_agent_list_cache(tenant_id: str, user_id: str, agents: list):
    """缓存用户可见的 Agent 列表。"""
    await set_cached("agent-list", CACHE_TTL_AGENT, agents, tenant_id=tenant_id, user_id=user_id)


async def get_agent_detail_cache(agent_id: int) -> Optional[dict]:
    """获取单个 Agent 详情缓存（包括绑定关系）。"""
    return await get_cached("agent-detail", CACHE_TTL_AGENT, agent_id=agent_id)


async def set_agent_detail_cache(agent_id: int, agent: dict):
    """缓存 Agent 详情。"""
    await set_cached("agent-detail", CACHE_TTL_AGENT, agent, agent_id=agent_id)


async def invalidate_agent_cache(tenant_id: str):
    """Agent 变更时清除相关缓存。

    删除该租户下所有 agent-list 和 agent-detail 缓存。
    """
    await invalidate_pattern(f"lea-config:agent-list:*")
    await invalidate_pattern(f"lea-config:agent-detail:*")


async def get_skill_list_cache(tenant_id: str, user_id: str) -> Optional[list]:
    """获取 Skill 列表缓存。"""
    return await get_cached("skill-list", CACHE_TTL_SKILL, tenant_id=tenant_id, user_id=user_id)


async def set_skill_list_cache(tenant_id: str, user_id: str, skills: list):
    """缓存 Skill 列表。"""
    await set_cached("skill-list", CACHE_TTL_SKILL, skills, tenant_id=tenant_id, user_id=user_id)


async def invalidate_skill_cache():
    """Skill 变更时清除所有 skill-list 缓存。"""
    await invalidate_pattern(f"lea-config:skill-list:*")


async def get_tool_list_cache(tenant_id: str, user_id: str) -> Optional[list]:
    """获取 Tool 列表缓存。"""
    return await get_cached("tool-list", CACHE_TTL_TOOL, tenant_id=tenant_id, user_id=user_id)


async def set_tool_list_cache(tenant_id: str, user_id: str, tools: list):
    """缓存 Tool 列表。"""
    await set_cached("tool-list", CACHE_TTL_TOOL, tools, tenant_id=tenant_id, user_id=user_id)


async def invalidate_tool_cache():
    """Tool 变更时清除所有 tool-list 缓存。"""
    await invalidate_pattern(f"lea-config:tool-list:*")


async def get_mcp_list_cache(tenant_id: str, user_id: str) -> Optional[list]:
    """获取 MCP Server 列表缓存。"""
    return await get_cached("mcp-list", CACHE_TTL_MCP, tenant_id=tenant_id, user_id=user_id)


async def set_mcp_list_cache(tenant_id: str, user_id: str, mcps: list):
    """缓存 MCP Server 列表。"""
    await set_cached("mcp-list", CACHE_TTL_MCP, mcps, tenant_id=tenant_id, user_id=user_id)


async def invalidate_mcp_cache():
    """MCP Server 变更时清除所有 mcp-list 缓存。"""
    await invalidate_pattern(f"lea-config:mcp-list:*")


async def get_team_cache(team_id: int) -> Optional[dict]:
    """获取 Team 配置缓存。"""
    return await get_cached("team", CACHE_TTL_TEAM, team_id=team_id)


async def set_team_cache(team_id: int, team: dict):
    """缓存 Team 配置。"""
    await set_cached("team", CACHE_TTL_TEAM, team, team_id=team_id)


async def invalidate_team_cache(team_id: int):
    """Team 变更时清除缓存。"""
    await invalidate_cached("team", team_id=team_id)
