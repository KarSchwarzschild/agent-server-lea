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


class PlatformState(TypedDict):
    """Supervisor编排图的共享状态。"""

    messages: Annotated[list[AnyMessage], add_messages]
    target_app: str
    target_agent: str
    user_id: str
    session_metadata: dict[str, Any]
