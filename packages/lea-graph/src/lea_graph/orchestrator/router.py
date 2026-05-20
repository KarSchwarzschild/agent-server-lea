"""
意图路由 — 分析用户消息，决定路由到哪个 App:Agent。

采用级联策略：关键词匹配（快、确定性高） → LLM分类（慢、覆盖广）。
"""

from __future__ import annotations

import re
from typing import Any

import structlog
from langchain_core.messages import HumanMessage
from langgraph.config import get_stream_writer

from langchain_core.runnables import RunnableConfig

from lea_graph.state import PlatformState

logger = structlog.get_logger()


def _extract_user_text(state: PlatformState) -> str:
    """从状态中提取最后一条用户消息的文本。"""
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            return msg.content
    return ""


def _keyword_match(
    text: str,
    app_skills: dict[str, dict[str, list[str]]],
) -> tuple[str, str, float] | None:
    """基于关键词的快速路由。

    Args:
        text: 用户输入文本
        app_skills: {app_name: {agent_name: [trigger_keywords]}}

    Returns:
        (app_name, agent_name, confidence) 或 None
    """
    text_lower = text.lower()
    best: tuple[str, str, float] | None = None

    for app_name, agents in app_skills.items():
        for agent_name, keywords in agents.items():
            for kw in keywords:
                if kw in text_lower:
                    confidence = min(0.7 + len(kw) * 0.05, 0.95)
                    if best is None or confidence > best[2]:
                        best = (app_name, agent_name, confidence)

    return best


async def route_message(state: PlatformState, config: RunnableConfig) -> dict:
    """路由节点：分析意图，设置 target_app + target_agent。

    先用关键词匹配做快速路由；如果匹配不到，走 fallback_qa。
    后续可扩展：在关键词和fallback之间加入LLM分类器。
    """
    writer = get_stream_writer()
    text = _extract_user_text(state)

    writer({"type": "thinking", "content": "正在分析您的意图..."})

    app_skills = config.get("configurable", {}).get("app_skills", {})
    match = _keyword_match(text, app_skills)

    if match:
        app_name, agent_name, confidence = match
        logger.info("intent_matched", app=app_name, agent=agent_name, confidence=confidence)
        writer({
            "type": "intent",
            "app": app_name,
            "agent": agent_name,
            "confidence": confidence,
        })
        return {"target_app": app_name, "target_agent": agent_name}

    writer({"type": "intent", "app": "fallback", "agent": "qa", "confidence": 0.5})
    return {"target_app": "fallback", "target_agent": "qa"}


def pick_agent(state: PlatformState) -> str:
    """条件边函数：根据 target_app:target_agent 选择下一个节点。"""
    app = state.get("target_app", "fallback")
    agent = state.get("target_agent", "qa")

    if app == "fallback":
        return "fallback_qa"
    return f"{app}__{agent}"
