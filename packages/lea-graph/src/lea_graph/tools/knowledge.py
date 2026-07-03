"""公共 Tool: 知识库搜索。

自动搜索当前用户可见的所有知识库（private + shared + public）。
也支持通过 RunnableConfig 传入指定 kb_ids（如智能笔记场景）。
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.config import get_stream_writer

from lea_graph.services.knowledge_base import search_knowledge_base


@tool
async def kb_search(query: str, limit: int = 5, *, config: RunnableConfig = None) -> str:
    """搜索知识库中的文献资料。根据查询关键词从用户可见的所有知识库中检索。"""
    writer = get_stream_writer()
    writer({"type": "action", "tool": "kb-search", "input": {"query": query, "limit": limit}})

    kb_ids = None
    document_ids = None
    tenant_id = ""
    user_id = ""

    if config and config.get("configurable", {}):
        cfg = config["configurable"]
        kb_ids = cfg.get("kb_ids")
        document_ids = cfg.get("document_ids")

    if not kb_ids and not document_ids:
        try:
            from lea_server.core.tenant import get_tenant
            ctx = get_tenant()
            tenant_id = ctx.tenant_id
            user_id = ctx.extra.get("user_id", "")
        except Exception:
            pass

    results = await search_knowledge_base(
        query, limit,
        kb_ids=kb_ids,
        document_ids=document_ids,
        tenant_id=tenant_id,
        user_id=user_id,
    )

    if results:
        writer({
            "type": "references",
            "references": [
                {
                    "index": i,
                    "title": r.get("title") or f"文档 {r.get('document_id') or '?'}",
                    "score": round(float(r.get("score", 0.0)), 4),
                    "kb_id": r.get("kb_id"),
                    "document_id": r.get("document_id"),
                    "page": r.get("page"),
                    "snippet": (r.get("content") or "")[:200],
                }
                for i, r in enumerate(results, 1)
            ],
            "mode": "append",
        })

    writer({"type": "observation", "tool": "kb-search", "status": "success", "count": len(results)})

    if not results:
        return "未找到相关文献。"

    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "未知标题")
        content = r.get("content", "")[:300]
        lines.append(f"[{i}] {title}\n{content}")

    return "\n\n".join(lines)
