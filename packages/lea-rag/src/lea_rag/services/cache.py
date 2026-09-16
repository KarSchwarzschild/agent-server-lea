"""向量检索结果缓存 — 缓存重复查询的检索和 Rerank 结果。

向量检索和重排序计算开销大，相同问题重复查询适合缓存：
- 向量检索结果（Milvus 查询）
- Rerank 结果（重排序模型推理）

知识库更新时清除相关缓存。
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
CACHE_TTL_RETRIEVAL = 1800  # 30分钟 - 检索结果
CACHE_TTL_RERANK = 1800     # 30分钟 - Rerank 结果
CACHE_TTL_NULL = 60         # 1分钟 - 空值缓存（防穿透）


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
            log.info("redis not configured, rag cache disabled")
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
    """标记 Redis 不可用。"""
    global _redis_available
    if _redis_available:
        _redis_available = False
        log.warning("redis unavailable, rag cache disabled")


def _cache_key(prefix: str, **kwargs) -> str:
    """生成缓存键：lea-rag:<prefix>:<hash>。

    使用内容哈希避免参数顺序影响。
    """
    raw = json.dumps(kwargs, sort_keys=True)
    h = hashlib.md5(raw.encode()).hexdigest()[:12]
    return f"lea-rag:{prefix}:{h}"


def _query_hash(query_text: str) -> str:
    """对查询文本生成短哈希（用于日志和键生成）。"""
    return hashlib.md5(query_text.encode()).hexdigest()[:8]


async def get_retrieval_cache(
    query_text: str,
    kb_id: int,
    top_k: int,
    strategy: str
) -> Optional[list]:
    """获取向量检索结果缓存。

    Args:
        query_text: 查询文本
        kb_id: 知识库 ID
        top_k: 返回文档数
        strategy: 检索策略（semantic/keyword/hybrid）

    Returns:
        缓存的文档列表，未命中返回 None
    """
    redis = get_redis()
    if redis is None:
        return None

    key = _cache_key("retrieval", query=query_text, kb_id=kb_id, top_k=top_k, strategy=strategy)
    try:
        value = await redis.get(key)
        if value:
            # 检查空值标记（防穿透）
            if value == "NULL":
                log.debug(
                    "retrieval cache hit (null marker)",
                    query_hash=_query_hash(query_text),
                    kb_id=kb_id
                )
                return None
            log.debug(
                "retrieval cache hit",
                query_hash=_query_hash(query_text),
                kb_id=kb_id,
                strategy=strategy
            )
            return json.loads(value)
        else:
            log.debug(
                "retrieval cache miss",
                query_hash=_query_hash(query_text),
                kb_id=kb_id
            )
            return None
    except Exception as e:
        log.warning("retrieval cache read failed", error=str(e))
        _mark_unavailable()
        return None


async def set_retrieval_cache(
    query_text: str,
    kb_id: int,
    top_k: int,
    strategy: str,
    results: list | None
):
    """缓存向量检索结果。

    Args:
        query_text: 查询文本
        kb_id: 知识库 ID
        top_k: 返回文档数
        strategy: 检索策略
        results: 检索结果列表，None 或空列表时写入空值标记（防穿透）
    """
    redis = get_redis()
    if redis is None:
        return

    key = _cache_key("retrieval", query=query_text, kb_id=kb_id, top_k=top_k, strategy=strategy)
    try:
        # 空值使用特殊标记 + 短 TTL（防穿透）
        if results is None or len(results) == 0:
            await redis.setex(key, CACHE_TTL_NULL, "NULL")
            log.debug(
                "retrieval cache set (null marker)",
                query_hash=_query_hash(query_text),
                kb_id=kb_id,
                ttl=CACHE_TTL_NULL
            )
        else:
            # 正常值加抖动 TTL（防雪崩）
            actual_ttl = _ttl_with_jitter(CACHE_TTL_RETRIEVAL)
            await redis.setex(key, actual_ttl, json.dumps(results))
            log.debug(
                "retrieval cache set",
                query_hash=_query_hash(query_text),
                kb_id=kb_id,
                strategy=strategy,
                ttl=actual_ttl
            )
    except Exception as e:
        log.warning("retrieval cache write failed", error=str(e))
        _mark_unavailable()


async def get_rerank_cache(query_text: str, doc_ids: list[str]) -> Optional[list]:
    """获取 Rerank 结果缓存。

    Args:
        query_text: 查询文本
        doc_ids: 候选文档 ID 列表（排序后再哈希，保证一致性）

    Returns:
        重排序后的文档列表，未命中返回 None
    """
    redis = get_redis()
    if redis is None:
        return None

    # 排序 doc_ids 保证缓存键一致性
    sorted_doc_ids = sorted(doc_ids)
    key = _cache_key("rerank", query=query_text, doc_ids=sorted_doc_ids)
    try:
        value = await redis.get(key)
        if value:
            log.debug("rerank cache hit", query_hash=_query_hash(query_text))
            return json.loads(value)
        else:
            log.debug("rerank cache miss", query_hash=_query_hash(query_text))
            return None
    except Exception as e:
        log.warning("rerank cache read failed", error=str(e))
        _mark_unavailable()
        return None


async def set_rerank_cache(query_text: str, doc_ids: list[str], results: list):
    """缓存 Rerank 结果。

    Args:
        query_text: 查询文本
        doc_ids: 候选文档 ID 列表
        results: 重排序后的结果
    """
    redis = get_redis()
    if redis is None:
        return

    sorted_doc_ids = sorted(doc_ids)
    key = _cache_key("rerank", query=query_text, doc_ids=sorted_doc_ids)
    try:
        await redis.setex(key, CACHE_TTL_RERANK, json.dumps(results))
        log.debug("rerank cache set", query_hash=_query_hash(query_text), ttl=CACHE_TTL_RERANK)
    except Exception as e:
        log.warning("rerank cache write failed", error=str(e))
        _mark_unavailable()


async def invalidate_kb_cache(kb_id: int):
    """知识库更新时清除相关检索缓存。

    删除所有包含该 kb_id 的检索缓存。

    注意：由于缓存键使用哈希，无法精确匹配 kb_id，
    这里删除所有 retrieval 缓存。生产环境可考虑：
    1. 在键中明文包含 kb_id（牺牲键长度）
    2. 维护 kb_id -> keys 的映射
    3. 设置较短 TTL 让缓存自然过期
    """
    redis = get_redis()
    if redis is None:
        return

    try:
        deleted = 0
        pattern = f"lea-rag:retrieval:*"
        async for key in redis.scan_iter(match=pattern, count=100):
            await redis.delete(key)
            deleted += 1
        if deleted > 0:
            log.info("kb cache invalidated", kb_id=kb_id, deleted=deleted)
    except Exception as e:
        log.warning("kb cache invalidation failed", kb_id=kb_id, error=str(e))


async def invalidate_all_retrieval_cache():
    """清除所有检索缓存（知识库批量更新时使用）。"""
    redis = get_redis()
    if redis is None:
        return

    try:
        deleted = 0
        pattern = f"lea-rag:retrieval:*"
        async for key in redis.scan_iter(match=pattern, count=100):
            await redis.delete(key)
            deleted += 1
        if deleted > 0:
            log.info("all retrieval cache invalidated", deleted=deleted)
    except Exception as e:
        log.warning("retrieval cache invalidation failed", error=str(e))
