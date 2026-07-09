"""invoke_skill — 多 Agent 协同基础工具。

让主 Agent 在执行过程中委托另一个 Skill 处理子任务。
被委托的 Skill 以 ReAct Agent 方式独立执行，拿到结果后返回给调用方。

SSE 事件：sub_agent_start → sub_agent_content → sub_agent_complete
前端根据这些事件渲染"子 Agent 执行中"的 UI 卡片。
"""

from __future__ import annotations

import asyncio
import uuid

import structlog
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.config import get_config, get_stream_writer
from langgraph.prebuilt import create_react_agent

from lea_graph.llm import get_chat_model
from lea_graph.skills.schema import ModelTier, SkillDefinition

logger = structlog.get_logger()

_TIER_TO_PURPOSE = {
    ModelTier.HIGH: "chat",
    ModelTier.MID: "routing",
    ModelTier.LOW: "fallback",
}

_MAX_DEPTH = 3


@tool
async def invoke_skill(skill_name: str, message: str) -> str:
    """委托另一个技能处理子任务。

    当你需要让专门的技能来完成某个子任务时使用此工具。
    例如：需要生成PPT时委托给 slides-generator，需要搜索资料时委托给 kb-search-skill。

    Args:
        skill_name: 要调用的技能名称（必须是当前 agent 已绑定的技能）
        message: 传给子技能的任务描述/问题
    """
    config = get_config()
    configurable = config.get("configurable", {})

    skill_definitions: dict[str, SkillDefinition] = configurable.get(
        "skill_definitions", {}
    )
    all_tools: dict = configurable.get("all_tools", {})
    current_depth = configurable.get("_invoke_depth", 0)

    if current_depth >= _MAX_DEPTH:
        return f"错误：子技能调用深度超限（最大 {_MAX_DEPTH} 层），请直接回答。"

    definition = skill_definitions.get(skill_name)
    if definition is None:
        available = list(skill_definitions.keys())[:10]
        return (
            f"错误：技能 '{skill_name}' 不存在或未绑定。"
            f"可用技能：{available}"
        )

    writer = get_stream_writer()
    call_id = uuid.uuid4().hex[:8]

    writer({
        "type": "sub_agent_start",
        "call_id": call_id,
        "skill_name": skill_name,
        "display_name": definition.display_name or skill_name,
        "message": message[:200],
    })

    try:
        result_text = await _execute_skill_inline(
            definition, message, all_tools, current_depth
        )
    except Exception as e:
        logger.error(
            "invoke_skill_error", skill=skill_name, error=str(e)
        )
        writer({
            "type": "sub_agent_error",
            "call_id": call_id,
            "skill_name": skill_name,
            "error": str(e)[:300],
        })
        return f"子技能 '{skill_name}' 执行失败：{str(e)[:200]}"

    writer({
        "type": "sub_agent_complete",
        "call_id": call_id,
        "skill_name": skill_name,
        "result_preview": result_text[:300],
    })

    return result_text


async def _execute_skill_inline(
    definition: SkillDefinition,
    message: str,
    all_tools: dict,
    current_depth: int,
) -> str:
    """内联执行子 Skill，收集 AI 文本输出并返回。"""
    from lea_graph.skills.executor import SkillExecutor
    from lea_graph.skills.hydrator import hydrate_bundle
    from lea_graph.skills.runtime_context import enter_skill_context, exit_skill_context

    definition = hydrate_bundle(definition)

    executor = SkillExecutor(all_tools)
    tools = executor.get_allowed_tools(definition)

    purpose = _TIER_TO_PURPOSE.get(definition.model_tier, "chat")
    model = get_chat_model(purpose=purpose, streaming=False)

    agent = create_react_agent(
        model=model,
        tools=tools,
        prompt=definition.system_prompt or None,
    )

    input_state = {"messages": [HumanMessage(content=message)]}

    token = None
    if definition.bundle_dir and definition.scripts_paths:
        token = enter_skill_context(
            skill_name=definition.name,
            bundle_dir=definition.bundle_dir,
            bundle_hash=definition.bundle_hash or "",
            scripts_paths=definition.scripts_paths,
        )
    try:
        result = await agent.ainvoke(input_state)
    finally:
        if token is not None:
            exit_skill_context(token)

    messages = result.get("messages", [])
    ai_parts = [
        m.content for m in messages
        if isinstance(m, AIMessage) and m.content
    ]
    return ai_parts[-1] if ai_parts else "子技能未返回内容。"

