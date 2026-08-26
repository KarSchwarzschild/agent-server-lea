"""综述正文生成节点。"""

from __future__ import annotations

import structlog
from langchain_core.messages import HumanMessage

from lea_apps.research.prompts import GENERATE_REVIEW, REVISE_REVIEW
from lea_apps.research.state import ReviewPhase, ReviewState
from lea_graph.infra.token_tracking import track_tokens
from lea_graph.llm import get_chat_model

logger = structlog.get_logger()


def _summarize_analyses(analyses: list[dict]) -> str:
    lines = []
    for i, a in enumerate(analyses, 1):
        title = a.get("paper_title", "未知")
        question = a.get("research_question", "")
        method = a.get("methodology", "")
        findings = a.get("key_findings", "")
        points = a.get("quotable_points", [])
        pts = "; ".join(points[:3]) if points else ""
        lines.append(
            f"[{i}] {title}\n  研究问题: {question}\n  方法: {method}"
            f"\n  发现: {findings}\n  可引用: {pts}"
        )
    return "\n\n".join(lines)


@track_tokens
async def generate_node(state: ReviewState) -> dict:
    llm = get_chat_model("chat", temperature=0.7, max_tokens=8192)
    summary = _summarize_analyses(state.get("analyses", []))
    feedback = state.get("review_feedback", "")

    if feedback and state.get("review_draft"):
        prompt = REVISE_REVIEW.format(
            topic=state["topic"],
            current_draft=state["review_draft"][:6000],
            feedback=feedback,
            outline=state.get("outline", ""),
        )
    else:
        prompt = GENERATE_REVIEW.format(
            topic=state["topic"],
            requirements=state.get("requirements", ""),
            outline=state.get("outline", ""),
            analyses_summary=summary,
        )

    resp = await llm.ainvoke([HumanMessage(content=prompt)])
    logger.info("review_generated", length=len(resp.content), revised=bool(feedback))
    return {"review_draft": resp.content, "phase": ReviewPhase.GENERATE, "review_feedback": ""}
