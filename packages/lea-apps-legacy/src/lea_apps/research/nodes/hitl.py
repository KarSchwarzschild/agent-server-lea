"""HITL (Human-In-The-Loop) 节点 — interrupt() 暂停等待用户审批。

驳回时用户可提供 feedback，注入到对应阶段的 state 中，
下一轮重做时内容节点读取 feedback 做定向修改而非从头重来。
"""

from __future__ import annotations

import structlog
from langgraph.types import interrupt

from lea_apps.research.state import ReviewState

logger = structlog.get_logger()


def _parse_response(user_response) -> tuple[bool, str]:
    if isinstance(user_response, dict):
        return user_response.get("approved", True), user_response.get("feedback", "")
    return True, ""


async def hitl_search_node(state: ReviewState) -> dict:
    papers = state.get("search_results", [])

    user_response = interrupt({
        "stage": "search",
        "message": f"已检索到 {len(papers)} 篇文献，请审查",
        "papers": [
            {"title": p.get("title"), "abstract": p.get("abstract", "")[:200]}
            for p in papers[:20]
        ],
    })

    approved, feedback = _parse_response(user_response)
    logger.info("hitl_search", approved=approved, has_feedback=bool(feedback))
    if approved:
        return {"search_approved": True}
    return {"search_approved": False, "search_feedback": feedback}


async def hitl_analysis_node(state: ReviewState) -> dict:
    analyses = state.get("analyses", [])

    user_response = interrupt({
        "stage": "analysis",
        "message": f"已分析 {len(analyses)} 篇文献，请审查",
        "analyses_summary": [
            {"title": a.get("paper_title"), "relevance": a.get("relevance")}
            for a in analyses
        ],
    })

    approved, feedback = _parse_response(user_response)
    logger.info("hitl_analysis", approved=approved, has_feedback=bool(feedback))
    if approved:
        return {"analysis_approved": True}
    return {"analysis_approved": False, "analysis_feedback": feedback}


async def hitl_outline_node(state: ReviewState) -> dict:
    user_response = interrupt({
        "stage": "outline",
        "message": "大纲已生成，请审查。可在反馈中提供修改意见或直接给出修改后的大纲。",
        "outline": state.get("outline", ""),
    })

    approved, feedback = _parse_response(user_response)
    logger.info("hitl_outline", approved=approved, has_feedback=bool(feedback))
    if approved:
        return {"outline_approved": True}
    return {"outline_approved": False, "outline_feedback": feedback}


async def hitl_final_node(state: ReviewState) -> dict:
    user_response = interrupt({
        "stage": "final",
        "message": "综述已生成，请最终审查",
        "review_preview": state.get("review_draft", "")[:3000],
    })

    approved, feedback = _parse_response(user_response)
    logger.info("hitl_final", approved=approved, has_feedback=bool(feedback))
    if approved:
        return {"review_approved": True}
    return {"review_approved": False, "review_feedback": feedback}
