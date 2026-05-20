"""Chat App专属Skill: 网页搜索。"""

from __future__ import annotations

import structlog
from langchain_core.tools import tool
from langgraph.config import get_stream_writer

logger = structlog.get_logger()


@tool
async def web_search(query: str) -> str:
    """搜索网页获取最新信息。适用于需要时效性信息的查询。"""
    writer = get_stream_writer()
    writer({"type": "action", "tool": "web-search", "input": {"query": query}})

    # 骨架实现 — 后续对接实际搜索API
    result = f"[网页搜索] 关于「{query}」的搜索结果：\n\n这是骨架实现，后续将对接实际的搜索API（如SerpAPI、Tavily等）。"

    writer({"type": "observation", "tool": "web-search", "status": "success"})
    return result
