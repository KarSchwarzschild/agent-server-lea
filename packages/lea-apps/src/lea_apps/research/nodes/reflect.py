"""自我反思节点 — 每阶段 LLM 自评，决定是否需要重做。"""

from __future__ import annotations

import json

import structlog
from langchain_core.messages import HumanMessage

from lea_apps.research.prompts import REFLECT
from lea_apps.research.state import ReviewPhase, ReviewState
from lea_graph.llm import get_chat_model

logger = structlog.get_logger()

MAX_REFLECTIONS = 2

_STAGE_MAP: dict[ReviewPhase, tuple[str, str, str]] = {
    ReviewPhase.SEARCH: ("search_results", "search_reflection", "检索"),
    ReviewPhase.ANALYSIS: ("analyses", "analysis_reflection", "分析"),
    ReviewPhase.OUTLINE: ("outline", "outline_reflection", "大纲"),
    ReviewPhase.GENERATE: ("review_draft", "review_reflection", "综述"),
}


def _content_for_phase(state: ReviewState, phase: ReviewPhase) -> str:
    content_key = _STAGE_MAP[phase][0]
    content = state.get(content_key, "")
    if isinstance(content, list):
        return json.dumps(content, ensure_ascii=False, indent=2)
    return str(content)


async def reflect_node(state: ReviewState) -> dict:
    """通用反思节点，根据当前 phase 评估对应输出质量。"""
    phase = state.get("phase", ReviewPhase.SEARCH)
    if phase not in _STAGE_MAP:
        return {}

    _, reflection_key, stage_name = _STAGE_MAP[phase]
    count = state.get("reflection_count", 0) + 1

    content = _content_for_phase(state, phase)

    llm = get_chat_model("chat", temperature=0.2)
    prompt = REFLECT.format(
        stage=stage_name,
        topic=state["topic"],
        content=content[:6000],
    )
    resp = await llm.ainvoke([HumanMessage(content=prompt)])

    try:
        result = json.loads(resp.content)
    except json.JSONDecodeError:
        result = {"passed": True, "overall": 4.0, "suggestions": []}

    if count >= MAX_REFLECTIONS:
        result["passed"] = True

    logger.info(
        "reflection_complete",
        stage=stage_name,
        overall=result.get("overall"),
        passed=result.get("passed"),
        attempt=count,
    )
    return {reflection_key: json.dumps(result, ensure_ascii=False), "reflection_count": count}


def reflection_passed(state: ReviewState) -> bool:
    """供条件边使用：当前阶段反思是否通过。"""
    phase = state.get("phase", ReviewPhase.SEARCH)
    if phase not in _STAGE_MAP:
        return True

    _, reflection_key, _ = _STAGE_MAP[phase]
    raw = state.get(reflection_key, "")
    if not raw:
        return True

    try:
        return json.loads(raw).get("passed", True)
    except (json.JSONDecodeError, TypeError):
        return True
