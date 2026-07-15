"""检索服务客户端 — lea-server 通过 HTTP 调用统一检索服务。

当 LEA_RETRIEVAL_SERVICE_URL 配置时，知识库检索和记忆检索走远程调用。
未配置时 fallback 到本地 import（embedded 模式）。
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()

_RETRIEVAL_URL = os.getenv("LEA_RETRIEVAL_SERVICE_URL", "")


def is_remote_mode() -> bool:
    return bool(_RETRIEVAL_URL)


async def kb_search(
    query: str,
    top_k: int = 5,
    *,
    kb_ids: list[int] | None = None,
    document_ids: list[int] | None = None,
    tenant_id: str = "",
    user_id: str = "",
) -> list[dict[str, Any]]:
    """知识库检索 — 远程模式走 HTTP，本地模式直接 import。"""
    if is_remote_mode():
        return await _remote_kb_search(
            query, top_k, kb_ids, document_ids, tenant_id
        )
    return await _local_kb_search(
        query, top_k, kb_ids, document_ids, tenant_id, user_id
    )


async def _remote_kb_search(
    query: str,
    top_k: int,
    kb_ids: list[int] | None,
    document_ids: list[int] | None,
    tenant_id: str,
) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {"query": query, "top_k": top_k}
    if kb_ids:
        payload["kb_ids"] = kb_ids
    if document_ids:
        payload["document_ids"] = document_ids
    if tenant_id:
        payload["tenant_id"] = tenant_id

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{_RETRIEVAL_URL}/kb/search", json=payload)
        resp.raise_for_status()
        return resp.json()


async def _remote_memory_search(
    user_id: str, query: str, top_k: int, tenant_id: str
) -> list[dict[str, Any]]:
    payload = {
        "user_id": user_id,
        "query": query,
        "top_k": top_k,
        "tenant_id": tenant_id,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{_RETRIEVAL_URL}/memory/search", json=payload
        )
        resp.raise_for_status()
        return resp.json()


async def _local_kb_search(
    query: str,
    top_k: int,
    kb_ids: list[int] | None,
    document_ids: list[int] | None,
    tenant_id: str,
    user_id: str,
) -> list[dict[str, Any]]:
    from lea_graph.services.knowledge_base import search_knowledge_base

    return await search_knowledge_base(
        query, top_k,
        kb_ids=kb_ids, document_ids=document_ids,
        tenant_id=tenant_id, user_id=user_id,
    )


async def _local_memory_search(
    user_id: str, query: str, top_k: int, tenant_id: str
) -> list[dict[str, Any]]:
    from lea_graph.memory.retriever import MemoryRetriever

    retriever = MemoryRetriever()
    results = await retriever.search(
        user_id=user_id, query=query,
        top_k=top_k, tenant_id=tenant_id,
    )
    return [
        {
            "id": m.id,
            "content": m.content,
            "category": m.category,
            "importance": m.importance,
            "score": m.score,
        }
        for m in results
    ]
