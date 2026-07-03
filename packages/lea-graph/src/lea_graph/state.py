"""
平台共享状态定义。

PlatformState 是 Supervisor 图和所有子 Agent 图共享的 TypedDict。
LangGraph 通过 add_messages 自动管理消息历史的追加与去重。
"""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


def merge_metadata(existing: dict[str, Any] | None, new: dict[str, Any] | None) -> dict[str, Any]:
    """自定义 reducer：空 dict 表示无变更，保留已有值；非空则合并。"""
    if not new:
        return existing or {}
    return {**(existing or {}), **new}


class PlatformState(TypedDict):
    """Supervisor编排图的共享状态。"""

    messages: Annotated[list[AnyMessage], add_messages]
    target_app: str
    target_agent: str
    target_skill: str
    user_id: str
    session_metadata: Annotated[dict[str, Any], merge_metadata]

    # 上下文管理
    context: dict[str, Any]
    user_memories: list[str]
