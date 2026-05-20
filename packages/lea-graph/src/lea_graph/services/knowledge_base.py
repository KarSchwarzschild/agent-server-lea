"""知识库检索服务 — 对接智能基座知识库API。"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()


@lru_cache
def _get_kb_config() -> tuple[str, str, str]:
    """获取知识库配置。"""
    from lea_graph.llm import _get_llm_settings

    s = _get_llm_settings()
    # 知识库ID从环境变量读取
    import os

    kb_id = os.getenv("LEA_DEFAULT_KB_ID", "")
    return s.tenant_api_base, s.tenant_api_key, kb_id


async def search_knowledge_base(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """检索知识库文档。

    Args:
        query: 搜索关键词
        limit: 返回结果数量上限

    Returns:
        文档列表，每个文档包含 title, content, score 等字段
    """
    api_base, api_key, kb_id = _get_kb_config()

    if not kb_id:
        logger.warning("kb_search_skipped", reason="no_kb_id_configured")
        return []

    url = f"{api_base}/v1/knowledge-bases/{kb_id}/search"
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {"query": query, "top_k": limit}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", data.get("results", []))
    except Exception as exc:
        logger.error("kb_search_error", error=str(exc))
        return []
