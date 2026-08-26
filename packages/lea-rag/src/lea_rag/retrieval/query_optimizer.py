"""查询优化 — Rewrite / Expand / HyDE 三策略管道。

参考 rnp 项目的 QueryOptimizer 模式，每种策略对应一个 LLM 调用，
通过策略列表灵活组合。

LLM 调用结果缓存到 Redis（TTL 30min），同一查询不重复调用。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from enum import Enum

import structlog
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from lea_rag.llm import get_chat_model

logger = structlog.get_logger(__name__)

_CACHE_TTL = 1800


class Strategy(str, Enum):
    REWRITE = "rewrite"
    EXPAND = "expand"
    HYDE = "hyde"


_REWRITE_PROMPT = """你是一个查询改写专家。将用户的口语化问题改写为更适合学术检索的规范表述。
只输出改写后的查询，不要解释。"""

_EXPAND_PROMPT = """你是一个查询扩展专家。将用户的问题分解为 {n} 个不同角度的子查询，覆盖不同方面。
每行一个查询，不要编号，不要解释。"""

_HYDE_PROMPT = """你是一个学术专家。针对以下问题，写一段假设性的学术文献摘要（约150字），
模拟一篇可能回答此问题的论文摘要。只输出摘要文本。"""


def _cache_key(strategy: str, query: str) -> str:
    h = hashlib.md5(query.encode()).hexdigest()[:12]
    return f"lea:qopt:{strategy}:{h}"


class QueryOptimizer:
    """查询优化管道 — 将原始查询转化为更适合检索的查询集合。"""

    def __init__(self, llm: BaseChatModel | None = None, redis_client=None):
        self._llm = llm or get_chat_model()
        self._redis = redis_client

    async def optimize(
        self,
        query: str,
        strategies: list[Strategy] | None = None,
    ) -> list[str]:
        """按策略顺序优化查询，返回扩展后的查询列表。"""
        strategies = strategies or [Strategy.REWRITE]
        queries = [query]

        for strategy in strategies:
            if strategy == Strategy.REWRITE:
                queries = [await self._rewrite(q) for q in queries]
            elif strategy == Strategy.EXPAND:
                expanded = await asyncio.gather(
                    *[self._expand(q) for q in queries]
                )
                queries = [q for group in expanded for q in group]
            elif strategy == Strategy.HYDE:
                hyde_docs = await asyncio.gather(
                    *[self._hyde(q) for q in queries]
                )
                queries = queries + list(hyde_docs)

        logger.info("query_optimized", original=query[:60], result_count=len(queries))
        return queries

    async def _get_cached(self, strategy: str, query: str) -> str | list[str] | None:
        if not self._redis:
            return None
        raw = self._redis.get(_cache_key(strategy, query))
        if raw is None:
            return None
        return json.loads(raw)

    def _set_cache(self, strategy: str, query: str, value: str | list[str]) -> None:
        if not self._redis:
            return
        self._redis.set(_cache_key(strategy, query), json.dumps(value, ensure_ascii=False), ex=_CACHE_TTL)

    async def _rewrite(self, query: str) -> str:
        cached = await self._get_cached("rewrite", query)
        if cached is not None:
            return cached

        resp = await self._llm.ainvoke([
            SystemMessage(content=_REWRITE_PROMPT),
            HumanMessage(content=query),
        ])
        result = resp.content.strip()
        self._set_cache("rewrite", query, result)
        return result

    async def _expand(self, query: str, n: int = 3) -> list[str]:
        cached = await self._get_cached("expand", query)
        if cached is not None:
            return cached

        resp = await self._llm.ainvoke([
            SystemMessage(content=_EXPAND_PROMPT.format(n=n)),
            HumanMessage(content=query),
        ])
        lines = [l.strip() for l in resp.content.strip().splitlines() if l.strip()]
        result = lines[:n]
        self._set_cache("expand", query, result)
        return result

    async def _hyde(self, query: str) -> str:
        cached = await self._get_cached("hyde", query)
        if cached is not None:
            return cached

        resp = await self._llm.ainvoke([
            SystemMessage(content=_HYDE_PROMPT),
            HumanMessage(content=query),
        ])
        result = resp.content.strip()
        self._set_cache("hyde", query, result)
        return result
