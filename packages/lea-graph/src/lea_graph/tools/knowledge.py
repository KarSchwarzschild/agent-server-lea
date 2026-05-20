"""公共Skill: 知识库搜索。"""

from __future__ import annotations

from langchain_core.tools import tool
from langgraph.config import get_stream_writer

from lea_graph.services.knowledge_base import search_knowledge_base


@tool
async def kb_search(query: str, limit: int = 5) -> str:
    """搜索知识库中的教育文献。根据查询关键词检索平台已有的文献资料。"""
    writer = get_stream_writer()
    writer({"type": "action", "tool": "kb-search", "input": {"query": query, "limit": limit}})

    results = await search_knowledge_base(query, limit)

    writer({"type": "observation", "tool": "kb-search", "status": "success", "count": len(results)})

    if not results:
        return "未找到相关文献。"

    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "未知标题")
        content = r.get("content", "")[:300]
        lines.append(f"[{i}] {title}\n{content}")

    return "\n\n".join(lines)
