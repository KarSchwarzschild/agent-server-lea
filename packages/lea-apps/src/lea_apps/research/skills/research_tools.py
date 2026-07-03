"""Research App 专属 Skill — 学术文献检索工具。"""

from __future__ import annotations

import structlog
from langchain_core.tools import tool

logger = structlog.get_logger()


@tool
async def academic_search_tool(query: str, limit: int = 10) -> str:
    """搜索多源学术数据库（万方/arXiv/Semantic Scholar），获取相关论文。"""
    from lea_rag.academic.merged import AcademicSearchClient

    client = AcademicSearchClient()
    try:
        result = await client.search(query, limit=limit)
    except Exception as exc:
        logger.warning("academic_search_tool_error", error=str(exc))
        return f"学术搜索出错: {exc}"

    if not result.papers:
        return f"未找到与「{query}」相关的学术文献。"

    lines = []
    for i, paper in enumerate(result.papers, 1):
        authors = ", ".join(paper.authors[:3])
        if len(paper.authors) > 3:
            authors += " 等"
        lines.append(
            f"[{i}] {paper.title}\n"
            f"    作者: {authors}\n"
            f"    来源: {paper.source.value}\n"
            f"    摘要: {paper.abstract[:200]}"
        )

    return f"找到 {len(result.papers)} 篇相关文献：\n\n" + "\n\n".join(lines)
