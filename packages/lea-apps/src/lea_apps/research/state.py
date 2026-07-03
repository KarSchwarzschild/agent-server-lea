"""综述生成 LangGraph 状态定义。"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


class ReviewPhase(str, Enum):
    INIT = "init"
    SEARCH = "search"
    ANALYSIS = "analysis"
    OUTLINE = "outline"
    GENERATE = "generate"
    FINALIZE = "finalize"


class ReviewState(TypedDict, total=False):
    # ── 输入 ──
    task_id: str
    tenant_id: str
    topic: str
    requirements: str

    # ── 检索阶段 ──
    sub_queries: list[str]
    search_results: list[dict]
    search_approved: bool
    search_feedback: str

    # ── 分析阶段 ──
    analyses: list[dict]
    analysis_approved: bool
    analysis_feedback: str

    # ── 大纲阶段 ──
    outline: str
    outline_approved: bool
    outline_feedback: str

    # ── 生成阶段 ──
    review_draft: str
    review_approved: bool
    review_feedback: str

    # ── 元数据 ──
    phase: ReviewPhase
    messages: Annotated[list, add_messages]
    error: str
