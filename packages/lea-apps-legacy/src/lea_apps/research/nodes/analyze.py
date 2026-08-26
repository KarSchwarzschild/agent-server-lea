"""文献分析节点：并发 LLM 分析每篇论文。"""

from __future__ import annotations

import asyncio
import json

import structlog
from langchain_core.messages import HumanMessage

from lea_apps.research.prompts import ANALYZE_PAPER
from lea_apps.research.state import ReviewPhase, ReviewState
from lea_graph.infra.token_tracking import track_tokens
from lea_graph.llm import get_chat_model

logger = structlog.get_logger()

CONCURRENCY = 3


@track_tokens
async def analyze_node(state: ReviewState) -> dict:
    """并发分析文献（信号量控制并发数）。"""
    llm = get_chat_model("chat", temperature=0.2)
    semaphore = asyncio.Semaphore(CONCURRENCY)
    topic = state["topic"]

    async def _analyze_one(paper: dict) -> dict:
        async with semaphore:
            prompt = ANALYZE_PAPER.format(
                title=paper.get("title", ""),
                authors=", ".join(paper.get("authors", [])),
                abstract=paper.get("abstract", ""),
                topic=topic,
            )
            try:
                resp = await llm.ainvoke([HumanMessage(content=prompt)])
                analysis = json.loads(resp.content)
            except json.JSONDecodeError:
                analysis = {"relevance": "medium", "key_findings": resp.content[:300]}
            except Exception as exc:
                logger.warning("analysis_failed", title=paper.get("title"), error=str(exc))
                analysis = {"relevance": "low", "error": str(exc)}

            analysis["paper_title"] = paper.get("title", "")
            return analysis

    papers = state.get("search_results", [])
    results = await asyncio.gather(*[_analyze_one(p) for p in papers])
    valid = [a for a in results if a.get("relevance") != "low" or "error" not in a]

    logger.info("analysis_complete", total=len(results), valid=len(valid))
    return {"analyses": valid, "phase": ReviewPhase.ANALYSIS}
