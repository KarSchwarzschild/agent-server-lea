"""Team Executor — 专家团并行协作节点。

流程：decompose → asyncio.gather（成员并行 ReAct）→ synthesize。
SSE 事件：team_start / team_decompose / team_member_start / team_member_done / team_synthesize_start / team_done。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.config import get_stream_writer
from langgraph.prebuilt import create_react_agent

from lea_graph.llm import get_chat_model
from lea_graph.orchestrator.builtin_tools import BUILTIN_TOOLS
from lea_graph.orchestrator.memory_nodes import build_messages_with_memories
from lea_graph.state import PlatformState

logger = structlog.get_logger()

_DEFAULT_DECOMPOSER_PROMPT = """\
你是一个任务分解专家。将用户请求分解为可以并行执行的子任务。

可用专家：
{members}

规则：
1. 每个子任务分配给最合适的专家
2. 子任务应独立可并行
3. 最多 {max_subtasks} 个子任务
4. 简单任务无需分解，用一个子任务即可
5. assigned_to 必须是上面列出的专家名称之一

只输出 JSON：{{"subtasks": [{{"description": "...", "assigned_to": "专家名"}}]}}"""

_DEFAULT_SYNTHESIZER_PROMPT = """\
你是综合分析师。多位专家从不同角度回答了用户问题。
请综合所有专家回复，给出完整、连贯的最终答案。
保留各专家关键见解，去除重复，适当标注观点来源。"""


async def team_executor_node(state: PlatformState, config) -> dict[str, Any]:
    """Team 执行节点：分解 → 并行 → 合成。"""
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    team_config: dict = configurable.get("team_config") or {}

    if not team_config:
        return {"messages": [AIMessage(content="未找到专家团配置。")]}

    writer = get_stream_writer()
    members = team_config.get("members", [])
    strategy = team_config.get("strategy", "parallel")

    writer({
        "type": "team_start",
        "team_name": team_config.get("team_name", "专家团"),
        "members": [
            {"name": m["name"], "avatar": m.get("avatar", ""), "role": m.get("role_description", "")}
            for m in members
        ],
    })

    user_text = _extract_user_text(state)

    # 1. Decompose
    subtasks = await _decompose(user_text, members, team_config, writer)

    # 2. Parallel execution
    results = await _parallel_run(subtasks, members, state, configurable, writer)

    # 3. Synthesize
    final_text = await _synthesize(user_text, results, team_config, writer)

    writer({"type": "team_done", "team_name": team_config.get("team_name", "专家团")})

    return {
        "messages": [AIMessage(content=final_text)],
        "team_subtask_results": results,
    }


def _extract_user_text(state: PlatformState) -> str:
    user_messages = [m.content for m in state["messages"] if isinstance(m, HumanMessage)]
    return user_messages[-1] if user_messages else ""


async def _decompose(
    user_text: str, members: list[dict], team_config: dict, writer
) -> list[dict]:
    """LLM 分解用户任务为子任务。"""
    members_desc = "\n".join(
        f"- {m['name']}：{m.get('role_description') or m.get('soul_prompt', '')[:80]}"
        for m in members
    )
    max_subtasks = team_config.get("max_subtasks", 5)

    prompt = (team_config.get("decomposer_prompt") or _DEFAULT_DECOMPOSER_PROMPT).format(
        members=members_desc, max_subtasks=max_subtasks
    )

    model = get_chat_model("routing", streaming=False, temperature=0)
    resp = await model.ainvoke([
        SystemMessage(content=prompt),
        HumanMessage(content=user_text),
    ])

    content = resp.content.strip()
    if "</think>" in content:
        content = content[content.rfind("</think>") + 8:].strip()

    try:
        start = content.find("{")
        end = content.rfind("}") + 1
        parsed = json.loads(content[start:end]) if start >= 0 else {}
    except (json.JSONDecodeError, ValueError):
        parsed = {}

    subtasks = parsed.get("subtasks", [])
    if not subtasks:
        subtasks = [{"description": user_text, "assigned_to": members[0]["name"]}]

    member_names = {m["name"] for m in members}
    for st in subtasks:
        if st.get("assigned_to") not in member_names:
            st["assigned_to"] = members[0]["name"]

    writer({
        "type": "team_decompose",
        "subtasks": [
            {"id": i, "description": st["description"], "assigned_to": st["assigned_to"]}
            for i, st in enumerate(subtasks)
        ],
    })

    logger.info("team_decomposed", count=len(subtasks))
    return subtasks


async def _parallel_run(
    subtasks: list[dict],
    members: list[dict],
    state: PlatformState,
    configurable: dict,
    writer,
) -> list[dict]:
    """并行执行各成员的子任务。"""
    member_map = {m["name"]: m for m in members}
    all_tools = configurable.get("all_tools", {})

    async def _run_one(idx: int, subtask: dict) -> dict:
        member_name = subtask["assigned_to"]
        member = member_map.get(member_name, members[0])

        writer({
            "type": "team_member_start",
            "subtask_id": idx,
            "member_name": member_name,
            "avatar": member.get("avatar", ""),
        })

        try:
            result_text = await _execute_member(member, subtask["description"], state, all_tools)
        except Exception as e:
            logger.error("team_member_error", member=member_name, error=str(e))
            result_text = f"执行出错：{str(e)[:200]}"

        writer({
            "type": "team_member_done",
            "subtask_id": idx,
            "member_name": member_name,
            "result_preview": result_text[:300],
        })

        return {
            "member_name": member_name,
            "role": member.get("role_description", ""),
            "subtask": subtask["description"],
            "result": result_text,
        }

    tasks = [_run_one(i, st) for i, st in enumerate(subtasks)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    final = []
    for r in results:
        if isinstance(r, Exception):
            final.append({"member_name": "unknown", "role": "", "subtask": "", "result": str(r)})
        else:
            final.append(r)
    return final


async def _execute_member(
    member: dict, subtask_desc: str, state: PlatformState, all_tools: dict
) -> str:
    """单个成员执行子任务。"""
    soul_prompt = member.get("soul_prompt", "")
    system = f"{soul_prompt}\n\n你是专家团成员，负责完成分配给你的子任务。" if soul_prompt else "你是专家团成员，负责完成分配给你的子任务。"

    tools = list(BUILTIN_TOOLS)
    member_skill_ids = set(member.get("skill_ids") or [])
    member_kb_ids = member.get("kb_ids") or []

    if member_kb_ids and "kb_search" in all_tools:
        tools.append(all_tools["kb_search"])

    non_kb_pool = {k: v for k, v in all_tools.items() if k != "kb_search"}
    tools.extend(non_kb_pool.values())

    model = get_chat_model("chat", streaming=False)
    agent = create_react_agent(model=model, tools=tools, prompt=system)

    messages = [HumanMessage(content=f"你的子任务：{subtask_desc}")]
    result = await agent.ainvoke({"messages": messages})

    ai_parts = [
        m.content for m in result.get("messages", [])
        if isinstance(m, AIMessage) and m.content
    ]
    return ai_parts[-1] if ai_parts else "未产出内容。"


async def _synthesize(
    user_text: str, results: list[dict], team_config: dict, writer
) -> str:
    """合成所有成员的结果。"""
    writer({"type": "team_synthesize_start"})

    system = team_config.get("synthesizer_prompt") or _DEFAULT_SYNTHESIZER_PROMPT
    context = "\n\n".join(
        f"== {r['member_name']}（{r['role']}）==\n{r['result']}"
        for r in results
    )

    model = get_chat_model("chat", streaming=False)
    resp = await model.ainvoke([
        SystemMessage(content=system),
        HumanMessage(content=f"用户问题：{user_text}\n\n专家回复：\n{context}"),
    ])
    return resp.content
