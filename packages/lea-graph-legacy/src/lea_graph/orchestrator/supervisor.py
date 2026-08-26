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
from lea_graph.orchestrator.security_node import security_gate, security_precheck_node
from lea_graph.orchestrator.summarization import build_summarization_node
from lea_graph.orchestrator.team_executor import team_executor_node
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


async def fallback_qa_node(state: PlatformState, config) -> dict[str, Any]:
    """Fallback 节点：带 builtin tools 的 ReAct agent。

    kb_search 只在 agent 绑了 KB 时才注入 —— 未绑则 LLM 上下文里根本看不到该工具。
    soul_prompt 从 configurable 注入 —— 用户自定义人格，拼在系统提示词前。
    """
    from langgraph.prebuilt import create_react_agent

    from lea_graph.llm import get_chat_model

    configurable = (
        config.get("configurable", {}) if isinstance(config, dict) else {}
    )
    all_tools = configurable.get("all_tools", {})
    agent_kb_ids = configurable.get("agent_kb_ids") or []

    tools = list(BUILTIN_TOOLS)
    if agent_kb_ids and "kb_search" in all_tools:
        tools.append(all_tools["kb_search"])

    model = get_chat_model("chat", streaming=True)

    soul_prompt = configurable.get("agent_soul_prompt") or ""
    system = f"{soul_prompt}\n\n{_FALLBACK_SYSTEM}" if soul_prompt else _FALLBACK_SYSTEM

    agent = create_react_agent(
        model=model,
        tools=tools,
        prompt=system,
    )

    messages = build_messages_with_memories(
        state["messages"], state.get("user_memories", [])
    )
    result = await agent.ainvoke({"messages": messages})
    return {"messages": result["messages"]}


async def skill_executor_node(state: PlatformState, config) -> dict[str, Any]:
    """Skill 执行节点：根据 execution_mode 选择 ReAct Agent 或 Pipeline。"""
    from langchain_core.messages import AIMessage, HumanMessage
    from langgraph.config import get_stream_writer
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

    # 通知前端 Skill 卡片起-止（对齐前端 useSSEHandler 白名单里的 skill_start/skill_done）
    writer = get_stream_writer()
    display_name = definition.display_name or definition.name
    writer({
        "type": "skill_start",
        "skill_name": definition.name,
        "display_name": display_name,
        "execution_mode": definition.execution_mode.value,
    })

    if definition.execution_mode == ExecutionMode.PIPELINE and definition.pipeline_class:
        result = await _run_skill_pipeline(state, definition, config)
        writer({"type": "skill_done", "skill_name": definition.name})
        return result

    all_tools = configurable.get("all_tools", {})
    agent_kb_ids = configurable.get("agent_kb_ids") or []

    # kb_search 独立走 agent 绑定门槛 —— 从默认池扣掉，绑了 KB 再单独注入
    non_kb_pool = {k: v for k, v in all_tools.items() if k != "kb_search"}
    if definition.allowed_tools:
        allowed = [non_kb_pool[n] for n in definition.allowed_tools if n in non_kb_pool]
    else:
        allowed = list(non_kb_pool.values())

    if agent_kb_ids and "kb_search" in all_tools:
        allowed.append(all_tools["kb_search"])

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

    # SG-6: 把当前 skill 的 bundle 上下文压栈，让 sandbox_exec 等 tool 能挂载 scripts
    from lea_graph.skills.runtime_context import enter_skill_context, exit_skill_context

    token = None
    if definition.bundle_dir and definition.scripts_paths:
        token = enter_skill_context(
            skill_name=definition.name,
            bundle_dir=definition.bundle_dir,
            bundle_hash=definition.bundle_hash or "",
            scripts_paths=definition.scripts_paths,
        )
    try:
        result = await agent.ainvoke({"messages": messages})
    finally:
        if token is not None:
            exit_skill_context(token)

    writer({"type": "skill_done", "skill_name": definition.name})
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
            writer({**event.data, "type": event.type})
            if event.type == "result":
                # pipeline 若显式传了 summary_text（用户可见文案），优先用它落 DB
                result_text = event.data.get("summary_text") or f"完成：{event.data.get('type', 'done')}"
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


_MAX_SKILL_LOOPS = 5


async def should_continue_node(state: PlatformState, config) -> dict:
    """判断 skill 执行完后任务是否完成，是否需要继续调另一个 skill。"""
    from langchain_core.messages import HumanMessage as HMsg, SystemMessage as SMsg
    from langgraph.config import get_stream_writer

    from lea_graph.llm import get_chat_model

    loop_count = (state.get("skill_loop_count") or 0) + 1

    if loop_count >= _MAX_SKILL_LOOPS:
        logger.info("skill_loop_max_reached", count=loop_count)
        return {"skill_loop_count": loop_count, "_skill_loop_done": True}

    user_messages = [
        m.content for m in state["messages"] if isinstance(m, HMsg)
    ]
    original_request = user_messages[0] if user_messages else ""

    ai_messages = [
        m.content for m in state["messages"]
        if isinstance(m, AIMessage) and m.content
    ]
    last_result = ai_messages[-1][:500] if ai_messages else ""

    model = get_chat_model("routing", streaming=False, temperature=0)
    resp = await model.ainvoke([
        SMsg(content=(
            "你是任务完成度判断器。用户提了一个请求，系统已经执行了一个步骤。\n"
            "判断：用户的原始请求是否已经完全满足？还是需要继续执行下一个步骤？\n\n"
            "只输出 JSON：{\"done\": true} 或 {\"done\": false, \"reason\": \"还需要做什么\"}\n"
            "注意：如果用户只要求做一件事且已完成，输出 done=true。\n"
            "如果用户要求做多件事（如先搜索再做PPT），已完成第一件但第二件未做，输出 done=false。"
        )),
        HMsg(content=(
            f"用户原始请求：{original_request}\n\n"
            f"已完成步骤的结果（摘要）：{last_result}"
        )),
    ])

    content = resp.content.strip()
    if "</think>" in content:
        content = content[content.rfind("</think>") + 8:].strip()

    import json as _json
    try:
        start = content.find("{")
        end = content.rfind("}") + 1
        parsed = _json.loads(content[start:end]) if start >= 0 else {}
    except Exception:
        parsed = {}

    is_done = parsed.get("done", True)
    reason = parsed.get("reason", "")

    if not is_done:
        writer = get_stream_writer()
        writer({"type": "thinking", "content": f"继续下一步：{reason}"})
        logger.info("skill_loop_continue", count=loop_count, reason=reason)

    return {"skill_loop_count": loop_count, "_skill_loop_done": is_done}


def _should_continue_gate(state: PlatformState) -> str:
    """条件边：skill 执行后决定回 router 还是走 memory_extract。"""
    if state.get("_skill_loop_done", True):
        return "memory_extract"
    return "router"


def build_supervisor_graph(
    app_agents: dict[str, dict[str, "CompiledStateGraph"]],
    checkpointer: BaseCheckpointSaver | None = None,
) -> "CompiledStateGraph":
    """构建 Supervisor 编排图（多 Skill 循环模式）。

    START → summarize → context_inject → security → router → executor
         → should_continue → (done: memory_extract → END | not done: router)
    """
    builder = StateGraph(PlatformState)

    summarization_node = build_summarization_node()
    builder.add_node("summarize", summarization_node)
    builder.add_node("context_inject", context_inject_node)
    builder.add_node("security_precheck", security_precheck_node)
    builder.add_node("router", route_message)
    builder.add_node("skill_executor", skill_executor_node)
    builder.add_node("should_continue", should_continue_node)
    builder.add_node("team_executor", team_executor_node)
    builder.add_node("fallback_qa", fallback_qa_node)
    builder.add_node("memory_extract", memory_extract_node)

    node_names: list[str] = []
    for app_name, agents in app_agents.items():
        for agent_name, graph in agents.items():
            node_id = f"{app_name}__{agent_name}"
            node_names.append(node_id)
            builder.add_node(node_id, graph)

    builder.add_edge(START, "summarize")
    builder.add_edge("summarize", "context_inject")
    builder.add_edge("context_inject", "security_precheck")
    builder.add_conditional_edges(
        "security_precheck", security_gate, {"router": "router", "END": END}
    )

    destinations = {n: n for n in node_names}
    destinations["fallback_qa"] = "fallback_qa"
    destinations["skill_executor"] = "skill_executor"
    destinations["team_executor"] = "team_executor"
    builder.add_conditional_edges("router", pick_agent, destinations)

    # skill_executor → should_continue → (router | memory_extract)
    builder.add_edge("skill_executor", "should_continue")
    builder.add_conditional_edges(
        "should_continue",
        _should_continue_gate,
        {"router": "router", "memory_extract": "memory_extract"},
    )

    # app agents 和 fallback_qa 不参与循环，直接走 memory
    builder.add_edge("team_executor", "memory_extract")
    for n in node_names:
        builder.add_edge(n, "memory_extract")
    builder.add_edge("fallback_qa", "memory_extract")
    builder.add_edge("memory_extract", END)

    logger.info(
        "supervisor_built",
        agent_nodes=node_names,
        total_agents=len(node_names),
    )

    return builder.compile(checkpointer=checkpointer)
