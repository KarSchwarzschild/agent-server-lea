"""RAG 相关节点：初始化、查询分解、多源检索。"""

from __future__ import annotations

import asyncio

import structlog
from langchain_core.messages import HumanMessage

from lea_apps.research.prompts import DECOMPOSE_TOPIC, REVISE_SEARCH_QUERIES
from lea_apps.research.state import ReviewPhase, ReviewState
from lea_graph.infra.token_tracking import track_tokens
from lea_graph.llm import get_chat_model

logger = structlog.get_logger()

MAX_RESULTS_PER_QUERY = 5


async def init_node(state: ReviewState) -> dict:
    return {
        "phase": ReviewPhase.INIT,
        "reflection_count": 0,
        "search_results": [],
        "analyses": [],
    }


@track_tokens
async def decompose_topic_node(state: ReviewState) -> dict:
    """将研究主题分解为 3-5 个检索子查询。"""
    llm = get_chat_model("chat", temperature=0.3)
    prompt = DECOMPOSE_TOPIC.format(
        topic=state["topic"],
        requirements=state.get("requirements", ""),
    )
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    queries = [q.strip() for q in response.content.strip().split("\n") if q.strip()]
    logger.info("topic_decomposed", topic=state["topic"], n_queries=len(queries))
    return {"sub_queries": queries, "phase": ReviewPhase.SEARCH}


@track_tokens
async def search_node(state: ReviewState) -> dict:
    """多源学术检索：并发搜索所有子查询，去重合并。"""
    feedback = state.get("search_feedback", "")
    sub_queries = state.get("sub_queries", [])

    if feedback and sub_queries:
        llm = get_chat_model("chat", temperature=0.3)
        prompt = REVISE_SEARCH_QUERIES.format(
            topic=state["topic"],
            requirements=state.get("requirements", ""),
            previous_queries="\n".join(sub_queries),
            feedback=feedback,
        )
        resp = await llm.ainvoke([HumanMessage(content=prompt)])
        sub_queries = [q.strip() for q in resp.content.strip().split("\n") if q.strip()]
        logger.info("search_queries_revised", feedback=feedback, n_queries=len(sub_queries))

    from lea_rag.academic.merged import AcademicSearchClient

    client = AcademicSearchClient()

    async def _search_one(query: str) -> list[dict]:
        try:
            result = await client.search(query, max_per_source=MAX_RESULTS_PER_QUERY)
            return [p.model_dump() for p in result.papers]
        except Exception as exc:
            logger.warning("search_query_failed", query=query, error=str(exc))
            return []

    tasks = [_search_one(q) for q in sub_queries]
    batches = await asyncio.gather(*tasks)

    seen_titles: set[str] = set()
    all_papers: list[dict] = []
    for batch in batches:
        for paper in batch:
            normalized = paper.get("title", "").lower().strip()
            if normalized and normalized not in seen_titles:
                seen_titles.add(normalized)
                all_papers.append(paper)

    logger.info("search_complete", total_papers=len(all_papers))
    return {
        "search_results": all_papers,
        "sub_queries": sub_queries,
        "search_feedback": "",
    }
