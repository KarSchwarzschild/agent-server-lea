"""Notebook RAG 对话 — 基于选定文档的检索增强生成。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import structlog

from lea_graph.llm import get_chat_model
from lea_rag.retrieval.hybrid import HybridRetriever

logger = structlog.get_logger()

RAG_SYSTEM_PROMPT = (
    "你是一个智能学习助手，根据用户上传的文档内容回答问题。\n"
    "使用以下检索到的文档片段来回答。如果片段中没有相关信息，如实告知。\n"
    "回答时引用来源编号，如 [1]、[2]。\n\n"
    "检索到的文档片段：\n{context}\n"
)


@dataclass
class ChatEvent:
    type: str
    data: dict[str, Any] = field(default_factory=dict)


def _get_retriever() -> HybridRetriever:
    from lea_apps.notebook.services.indexer import get_notebook_index
    return HybridRetriever(index=get_notebook_index())


def _build_context(nodes) -> tuple[str, list[dict]]:
    context_parts: list[str] = []
    sources: list[dict] = []

    for i, node in enumerate(nodes, 1):
        text = node.node.get_content()
        meta = node.node.metadata or {}
        context_parts.append(f"[{i}] {text}")
        sources.append({
            "index": i,
            "content": text[:200],
            "page": meta.get("page", 0),
            "source_id": meta.get("source_id"),
            "score": round(node.score, 4) if node.score else None,
        })

    return "\n\n".join(context_parts), sources


async def chat_stream(
    question: str,
    source_ids: list[int] | None = None,
    history: list[dict[str, str]] | None = None,
    kb_ids: list[int] | None = None,
    document_ids: list[int] | None = None,
) -> AsyncIterator[ChatEvent]:
    """RAG 对话，yield 结构化事件（由调用方转 SSE）。

    优先使用平台知识库检索（kb_ids/document_ids），
    如果未指定则回退到 Notebook 本地索引（source_ids）。
    """
    if kb_ids or document_ids:
        from lea_graph.services.knowledge_base import search_knowledge_base
        raw_results = await search_knowledge_base(
            question, limit=5, kb_ids=kb_ids, document_ids=document_ids,
        )
        if not raw_results:
            yield ChatEvent("content", {"text": "未在文档中找到相关内容，请尝试换个问法或检查文档是否已解析完成。"})
            yield ChatEvent("sources", {"items": []})
            yield ChatEvent("done", {"status": "completed"})
            return

        context_parts = []
        sources = []
        for i, r in enumerate(raw_results, 1):
            context_parts.append(f"[{i}] {r['content']}")
            sources.append({
                "index": i,
                "content": r["content"][:200],
                "page": r.get("page", 0),
                "source_id": r.get("document_id"),
                "score": r.get("score"),
            })
        context = "\n\n".join(context_parts)
    else:
        retriever = _get_retriever()
        result = await retriever.aretrieve(question, source_ids=source_ids)

        if not result.nodes:
            yield ChatEvent("content", {"text": "未在文档中找到相关内容，请尝试换个问法或检查文档是否已解析完成。"})
            yield ChatEvent("sources", {"items": []})
            yield ChatEvent("done", {"status": "completed"})
            return

        context, sources = _build_context(result.nodes)

    yield ChatEvent("sources", {"items": sources})

    system_msg = RAG_SYSTEM_PROMPT.format(context=context)

    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    lc_messages = [SystemMessage(content=system_msg)]
    if history:
        for m in history[-6:]:
            if m["role"] == "user":
                lc_messages.append(HumanMessage(content=m["content"]))
            elif m["role"] == "assistant":
                lc_messages.append(AIMessage(content=m["content"]))
    lc_messages.append(HumanMessage(content=question))

    llm = get_chat_model("chat", streaming=True, temperature=0.7)

    try:
        async for chunk in llm.astream(lc_messages):
            if chunk.content:
                yield ChatEvent("content", {"text": chunk.content})
    except Exception as exc:
        logger.error("chat_stream_error", error=str(exc))
        yield ChatEvent("error", {"message": str(exc)})

    yield ChatEvent("done", {"status": "completed"})
