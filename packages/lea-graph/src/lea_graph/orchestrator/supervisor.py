"""
Supervisor 编排图 — 对话界面的统一入口。

用户消息 → router(意图分析) → app__agent(执行) → END

仅用于 /chat 对话入口。App独立页面的API直接调用App内的Agent，
不经过此 Supervisor 图。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from lea_graph.orchestrator.router import pick_agent, route_message
from lea_graph.state import PlatformState

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

logger = structlog.get_logger()


async def fallback_qa_node(state: PlatformState) -> dict[str, Any]:
    """Fallback节点：当无法路由到具体Agent时，返回通用回复。"""
    from lea_graph.llm import get_chat_model

    model = get_chat_model("chat", streaming=True)
    response = await model.ainvoke(state["messages"])
    return {"messages": [response]}


def build_supervisor_graph(
    app_agents: dict[str, dict[str, CompiledStateGraph]],
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """构建 Supervisor 编排图。

    Args:
        app_agents: {app_name: {agent_name: compiled_graph}}
        checkpointer: LangGraph Checkpointer（Redis），用于对话状态持久化

    Returns:
        编译后的 CompiledStateGraph

    架构说明:
        Supervisor 图是一个两层结构：
        1. router 节点分析意图，设置 target_app + target_agent
        2. 条件边根据 target 路由到对应的 App:Agent 子图
        3. 每个 App:Agent 是一个独立的 LangGraph 子图（通常是 ReAct Agent）
    """
    builder = StateGraph(PlatformState)

    builder.add_node("router", route_message)

    node_names: list[str] = []
    for app_name, agents in app_agents.items():
        for agent_name, graph in agents.items():
            node_id = f"{app_name}__{agent_name}"
            node_names.append(node_id)
            builder.add_node(node_id, graph)

    builder.add_node("fallback_qa", fallback_qa_node)

    builder.add_edge(START, "router")

    destinations = {n: n for n in node_names}
    destinations["fallback_qa"] = "fallback_qa"
    builder.add_conditional_edges("router", pick_agent, destinations)

    for n in node_names:
        builder.add_edge(n, END)
    builder.add_edge("fallback_qa", END)

    logger.info(
        "supervisor_built",
        agent_nodes=node_names,
        total_agents=len(node_names),
    )

    return builder.compile(checkpointer=checkpointer)
