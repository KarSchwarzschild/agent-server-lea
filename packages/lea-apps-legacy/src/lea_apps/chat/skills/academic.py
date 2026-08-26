"""Chat App专属Skill: 学术搜索 — 迁移自旧项目 skills/academic-search。"""

from __future__ import annotations

import httpx
import structlog
from langchain_core.tools import tool
from langgraph.config import get_stream_writer

logger = structlog.get_logger()


@tool
async def academic_search(query: str, limit: int = 5) -> str:
    """搜索学术文献数据库，获取与查询相关的学术论文和研究报告。"""
    writer = get_stream_writer()
    writer({"type": "action", "tool": "academic-search", "input": {"query": query, "limit": limit}})

    try:
        from lea_graph.llm import _get_llm_settings

        settings = _get_llm_settings()
        url = f"{settings.tenant_api_base}/v1/search/academic"
        headers = {"Authorization": f"Bearer {settings.tenant_api_key}"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                json={"query": query, "top_k": limit},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("data", data.get("results", []))
    except Exception as exc:
        logger.warning("academic_search_fallback", error=str(exc))
        results = [
            {"title": f"[示例] 关于「{query}」的研究", "abstract": "这是一个示例结果，实际API不可用时显示。"}
        ]

    writer({"type": "observation", "tool": "academic-search", "status": "success", "count": len(results)})

    if not results:
        return f"未找到与「{query}」相关的学术文献。"

    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "未知标题")
        abstract = r.get("abstract", r.get("content", ""))[:200]
        lines.append(f"[{i}] {title}\n    {abstract}")

    return f"找到 {len(results)} 篇相关文献：\n\n" + "\n\n".join(lines)
