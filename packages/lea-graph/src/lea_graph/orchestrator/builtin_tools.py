"""内置工具 — ReAct agent 自主调用来展示计划和向用户提问。

这些工具通过 SSE stream_writer 直接推送事件到前端，
不改变 graph state，只产生副作用（UI 事件）。
"""

from __future__ import annotations

from langchain_core.tools import tool
from langgraph.config import get_stream_writer

from lea_graph.tools.sandbox_exec import sandbox_exec


@tool
def show_plan(steps: list[str]) -> str:
    """展示或更新执行计划给用户。

    当你面对复杂任务时，先用此工具展示你的计划。
    执行过程中如果计划有调整，再次调用即可更新。

    Args:
        steps: 计划步骤列表，如 ["搜索相关资料", "整理要点", "生成PPT"]
    """
    writer = get_stream_writer()
    plan_data = [
        {"step": i + 1, "task": s, "status": "pending"}
        for i, s in enumerate(steps)
    ]
    writer({"type": "plan_created", "plan": plan_data})
    return f"已向用户展示计划（{len(steps)}个步骤）。请继续执行。"


@tool
def ask_user(question: str) -> str:
    """向用户提问以获取必要信息。

    当你需要用户澄清需求、确认选择或提供额外信息时调用此工具。
    调用后当前轮次结束，等待用户回复。

    Args:
        question: 要问用户的问题
    """
    writer = get_stream_writer()
    writer({"type": "ask_user", "question": question})
    return f"已向用户提问：{question}。等待用户回复。"


BUILTIN_TOOLS = [show_plan, ask_user, sandbox_exec]
