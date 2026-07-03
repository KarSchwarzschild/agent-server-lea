"""
Supervisor 编排图 — 对话界面的统一入口。

图结构（ReAct 动态规划）：
START → summarize → context_inject → router → executor → memory_extract → END

executor 是带 show_plan/ask_user 工具的 ReAct agent，
LLM 自主决定何时规划、何时追问、何时执行。

仅用于 /chat 对话入口。App独立页面的API直接调用App内的Agent。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from lea_graph.orchestrator.builtin_tools import BUILTIN_TOOLS
from lea_graph.orchestrator.memory_nodes import (
    build_messages_with_memories,
    context_inject_node,
    memory_extract_node,
)
from lea_graph.orchestrator.router import pick_agent, route_message
from lea_graph.orchestrator.summarization import build_summarization_node
from lea_graph.state import PlatformState

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

logger = structlog.get_logger()

_FALLBACK_SYSTEM = """\
你是一个教育科研助手。

行为准则：
- 面对复杂任务（如生成PPT、写报告、做综述），先用 show_plan 展示计划
- 如果用户没给够关键信息（受众、页数、重点等），用 ask_user 追问
- 简单问答直接回答，不需要规划
- 执行过程中如果计划需要调整，再次调用 show_plan 更新
"""


async def fallback_qa_node(state: PlatformState) -> dict[str, Any]:
    """Fallback 节点：带 builtin tools 的 ReAct agent。"""
    from langgraph.prebuilt import create_react_agent

    from lea_graph.llm import get_chat_model

    model = get_chat_model("chat", streaming=True)
    agent = create_react_agent(
        model=model,
        tools=BUILTIN_TOOLS,
        prompt=_FALLBACK_SYSTEM,
    )

    messages = build_messages_with_memories(
        state["messages"], state.get("user_memories", [])
    )
    result = await agent.ainvoke({"messages": messages})
    return {"messages": result["messages"]}


async def skill_executor_node(state: PlatformState, config) -> dict[str, Any]:
    """Skill 执行节点：根据 execution_mode 选择 ReAct Agent 或 Pipeline。"""
    from langchain_core.messages import AIMessage, HumanMessage
    from langgraph.prebuilt import create_react_agent

    from lea_graph.llm import get_chat_model
    from lea_graph.skills.schema import ExecutionMode, ModelTier

    skill_name = state.get("target_skill", "")
    configurable = (
        config.get("configurable", {}) if isinstance(config, dict) else {}
    )
    skill_definitions = configurable.get("skill_definitions", {})

    definition = skill_definitions.get(skill_name)
    if definition is None:
        return {"messages": [AIMessage(content=f"未找到技能「{skill_name}」")]}

    if definition.execution_mode == ExecutionMode.PIPELINE and definition.pipeline_class:
        return await _run_skill_pipeline(state, definition, config)

    all_tools = configurable.get("all_tools", {})
    allowed = list(all_tools.values())
    if definition.allowed_tools:
        allowed = [all_tools[n] for n in definition.allowed_tools if n in all_tools]
    # 所有 skill 也带 builtin tools
    allowed = BUILTIN_TOOLS + allowed

    tier_map = {ModelTier.HIGH: "chat", ModelTier.MID: "routing", ModelTier.LOW: "fallback"}
    purpose = tier_map.get(definition.model_tier, "chat")
    model = get_chat_model(purpose=purpose, streaming=True)

    prompt = definition.system_prompt or _FALLBACK_SYSTEM

    agent = create_react_agent(model=model, tools=allowed, prompt=prompt)
    messages = build_messages_with_memories(
        state["messages"], state.get("user_memories", [])
    )
    result = await agent.ainvoke({"messages": messages})
    return {"messages": result["messages"]}


async def _run_skill_pipeline(state: PlatformState, definition, config) -> dict[str, Any]:
    """执行 Pipeline 模式的 Skill，收集事件转为消息。"""
    from langchain_core.messages import AIMessage, HumanMessage
    from langgraph.config import get_stream_writer

    from lea_graph.skills.executor import SkillExecutor

    configurable = (
        config.get("configurable", {}) if isinstance(config, dict) else {}
    )
    all_tools = configurable.get("all_tools", {})
    executor = SkillExecutor(all_tools)
    writer = get_stream_writer()

    user_messages = [
        msg.content for msg in state["messages"] if isinstance(msg, HumanMessage)
    ]
    user_message = user_messages[-1] if user_messages else ""

    result_text = ""
    artifact_id = ""
    try:
        async for event in executor.execute_pipeline(
            definition,
            user_message,
            session_id=(
                configurable.get("thread_id", "").split(":")[-1]
                or uuid.uuid4().hex
            ),
            user_id=state.get("user_id", ""),
            messages=state.get("messages", []),
        ):
            writer({"type": event.type, **event.data})
            if event.type == "result":
                result_text = f"完成：{event.data.get('type', 'done')}"
                if event.data.get("artifact_id"):
                    artifact_id = event.data["artifact_id"]
            elif event.type == "error":
                result_text = f"执行出错：{event.data.get('message', '')}"
    except Exception as e:
        logger.error("skill_pipeline_error", skill=definition.name, error=str(e))
        result_text = f"Pipeline 执行异常: {str(e)}"

    result: dict[str, Any] = {
        "messages": [AIMessage(content=result_text or "Pipeline 执行完毕")],
    }
    if artifact_id:
        result["session_metadata"] = {"slides_artifact_id": artifact_id}
    return result


def build_supervisor_graph(
    app_agents: dict[str, dict[str, "CompiledStateGraph"]],
    checkpointer: BaseCheckpointSaver | None = None,
) -> "CompiledStateGraph":
    """构建 Supervisor 编排图（ReAct 动态规划模式）。

    START → summarize → context_inject → router → executor → memory_extract → END
    没有固定的 planner 节点，LLM 通过 tool 自主规划。
    长期记忆存 MySQL，不依赖 Redis Store。
    """
    builder = StateGraph(PlatformState)

    summarization_node = build_summarization_node()
    builder.add_node("summarize", summarization_node)
    builder.add_node("context_inject", context_inject_node)
    builder.add_node("router", route_message)
    builder.add_node("skill_executor", skill_executor_node)
    builder.add_node("fallback_qa", fallback_qa_node)
    builder.add_node("memory_extract", memory_extract_node)

    node_names: list[str] = []
    for app_name, agents in app_agents.items():
        for agent_name, graph in agents.items():
            node_id = f"{app_name}__{agent_name}"
            node_names.append(node_id)
            builder.add_node(node_id, graph)

    # START → summarize → context_inject → router
    builder.add_edge(START, "summarize")
    builder.add_edge("summarize", "context_inject")
    builder.add_edge("context_inject", "router")

    # router → 各执行器
    destinations = {n: n for n in node_names}
    destinations["fallback_qa"] = "fallback_qa"
    destinations["skill_executor"] = "skill_executor"
    builder.add_conditional_edges("router", pick_agent, destinations)

    # 各执行器 → memory_extract → END
    for n in node_names:
        builder.add_edge(n, "memory_extract")
    builder.add_edge("fallback_qa", "memory_extract")
    builder.add_edge("skill_executor", "memory_extract")
    builder.add_edge("memory_extract", END)

    logger.info(
        "supervisor_built",
        agent_nodes=node_names,
        total_agents=len(node_names),
    )

    return builder.compile(checkpointer=checkpointer)
