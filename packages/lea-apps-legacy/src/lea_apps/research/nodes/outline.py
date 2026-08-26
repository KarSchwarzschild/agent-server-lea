"""大纲生成节点。"""

from __future__ import annotations

import structlog
from langchain_core.messages import HumanMessage

from lea_apps.research.prompts import GENERATE_OUTLINE, REVISE_OUTLINE
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
        lines.append(
            f"[{i}] {title}\n  研究问题: {question}\n  方法: {method}\n  发现: {findings}"
        )
    return "\n\n".join(lines)


@track_tokens
async def outline_node(state: ReviewState) -> dict:
    llm = get_chat_model("chat", temperature=0.5)
    summary = _summarize_analyses(state.get("analyses", []))
    feedback = state.get("outline_feedback", "")

    if feedback and state.get("outline"):
        prompt = REVISE_OUTLINE.format(
            topic=state["topic"],
            current_outline=state["outline"],
            feedback=feedback,
            analyses_summary=summary,
        )
    else:
        prompt = GENERATE_OUTLINE.format(
            topic=state["topic"],
            requirements=state.get("requirements", ""),
            analyses_summary=summary,
        )

    resp = await llm.ainvoke([HumanMessage(content=prompt)])
    logger.info("outline_generated", length=len(resp.content), revised=bool(feedback))
    return {"outline": resp.content, "phase": ReviewPhase.OUTLINE, "outline_feedback": ""}
