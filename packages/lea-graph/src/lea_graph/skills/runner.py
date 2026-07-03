"""
Skill Runner — 将 SkillDefinition 转为可执行的 Agent/Pipeline，输出 SSE 流。

显式触发（用户 @ 或 + 选择 Skill）和自动路由（Supervisor 匹配到 Skill）
都通过此模块执行。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.prebuilt import create_react_agent

from lea_graph.llm import get_chat_model
from lea_graph.skills.executor import SkillExecutor
from lea_graph.skills.pipeline import SkillEvent
from lea_graph.skills.schema import ExecutionMode, ModelTier, SkillDefinition
from lea_graph.streaming import _sse

logger = structlog.get_logger()

_TIER_TO_PURPOSE = {
    ModelTier.HIGH: "chat",
    ModelTier.MID: "routing",
    ModelTier.LOW: "fallback",
}

_EVENT_MAP = {
    "progress": "thinking",
    "content": "content",
    "result": "artifact_done",
    "error": "error",
    "done": "done",
}


async def execute_skill_as_sse(
    definition: SkillDefinition,
    message: str,
    all_tools: dict[str, BaseTool],
    *,
    checkpointer=None,
    thread_id: str = "",
) -> AsyncIterator[str]:
    """执行 Skill 并输出 SSE 流。

    根据 execution_mode 选择执行方式：
    - react: 构建临时 ReAct Agent（LangGraph create_react_agent）
    - pipeline: 动态加载 SkillPipeline 子类
    """
    yield _sse("thinking", {"content": f"正在使用技能「{definition.display_name}」..."})

    if definition.execution_mode == ExecutionMode.PIPELINE and definition.pipeline_class:
        async for chunk in _run_pipeline(definition, message, all_tools):
            yield chunk
    else:
        async for chunk in _run_react(definition, message, all_tools, checkpointer, thread_id):
            yield chunk

    yield _sse("done", {"status": "completed"})


async def _run_react(
    definition: SkillDefinition,
    message: str,
    all_tools: dict[str, BaseTool],
    checkpointer,
    thread_id: str,
) -> AsyncIterator[str]:
    """ReAct 模式：构建临时 Agent 执行。"""
    executor = SkillExecutor(all_tools)
    tools = executor.get_allowed_tools(definition)

    purpose = _TIER_TO_PURPOSE.get(definition.model_tier, "chat")
    model = get_chat_model(purpose=purpose, streaming=True)

    agent = create_react_agent(
        model=model,
        tools=tools,
        prompt=definition.system_prompt or None,
    )

    input_state = {"messages": [HumanMessage(content=message)]}
    config: dict[str, Any] = {}
    if thread_id:
        config["configurable"] = {"thread_id": f"skill:{thread_id}"}

    from lea_graph.streaming import stream_graph_as_sse
    async for chunk in stream_graph_as_sse(agent, input_state, config):
        yield chunk


async def _run_pipeline(
    definition: SkillDefinition,
    message: str,
    all_tools: dict[str, BaseTool],
) -> AsyncIterator[str]:
    """Pipeline 模式：动态加载 SkillPipeline 并执行。"""
    executor = SkillExecutor(all_tools)

    try:
        async for event in executor.execute_pipeline(definition, message):
            sse_type = _EVENT_MAP.get(event.type, event.type)
            yield _sse(sse_type, event.data)
    except Exception as exc:
        logger.error("pipeline_run_error", skill=definition.name, error=str(exc))
        yield _sse("error", {"message": str(exc)})
