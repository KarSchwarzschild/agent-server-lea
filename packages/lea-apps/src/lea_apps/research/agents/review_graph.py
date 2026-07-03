"""综述生成 LangGraph 状态图 — HITL 人工审查。

状态图流程：
  START → init → decompose_topic → search → hitl_search
    → analyze → hitl_analysis
    → outline → hitl_outline
    → generate → hitl_final
    → finalize → END

每个阶段：
  1. 内容生成节点（search/analyze/outline/generate）
  2. HITL 节点（interrupt() 暂停，等用户审批）
     - 通过 → 下一阶段
     - 驳回 + 反馈 → 重做该阶段（feedback 注入 prompt）
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.graph import END, START, StateGraph

from lea_apps.research.nodes.analyze import analyze_node
from lea_apps.research.nodes.finalize import finalize_node
from lea_apps.research.nodes.generate import generate_node
from lea_apps.research.nodes.hitl import (
    hitl_analysis_node,
    hitl_final_node,
    hitl_outline_node,
    hitl_search_node,
)
from lea_apps.research.nodes.outline import outline_node
from lea_apps.research.nodes.rag import decompose_topic_node, init_node, search_node
from lea_apps.research.state import ReviewState

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.graph.state import CompiledStateGraph


def _route_after_hitl(state: ReviewState, key: str) -> str:
    return "approved" if state.get(key, True) else "redo"


def build_review_graph(
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    builder = StateGraph(ReviewState)

    # ── 节点注册 ──
    builder.add_node("init", init_node)
    builder.add_node("decompose_topic", decompose_topic_node)
    builder.add_node("search", search_node)
    builder.add_node("hitl_search", hitl_search_node)
    builder.add_node("analyze", analyze_node)
    builder.add_node("hitl_analysis", hitl_analysis_node)
    builder.add_node("outline", outline_node)
    builder.add_node("hitl_outline", hitl_outline_node)
    builder.add_node("generate", generate_node)
    builder.add_node("hitl_final", hitl_final_node)
    builder.add_node("finalize", finalize_node)

    # ── 线性边 ──
    builder.add_edge(START, "init")
    builder.add_edge("init", "decompose_topic")
    builder.add_edge("decompose_topic", "search")
    builder.add_edge("search", "hitl_search")
    builder.add_edge("analyze", "hitl_analysis")
    builder.add_edge("outline", "hitl_outline")
    builder.add_edge("generate", "hitl_final")
    builder.add_edge("finalize", END)

    # ── HITL 条件边（审批通过 → 下一阶段，驳回 → 带反馈重做） ──
    builder.add_conditional_edges(
        "hitl_search",
        lambda s: _route_after_hitl(s, "search_approved"),
        {"approved": "analyze", "redo": "search"},
    )
    builder.add_conditional_edges(
        "hitl_analysis",
        lambda s: _route_after_hitl(s, "analysis_approved"),
        {"approved": "outline", "redo": "analyze"},
    )
    builder.add_conditional_edges(
        "hitl_outline",
        lambda s: _route_after_hitl(s, "outline_approved"),
        {"approved": "generate", "redo": "outline"},
    )
    builder.add_conditional_edges(
        "hitl_final",
        lambda s: _route_after_hitl(s, "review_approved"),
        {"approved": "finalize", "redo": "generate"},
    )

    if checkpointer is None:
        raise ValueError("checkpointer is required — Redis must be available for HITL state persistence")

    return builder.compile(checkpointer=checkpointer)
