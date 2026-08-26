"""
意图路由 — 纯 LLM 语义路由，统一候选列表。

所有可用的 Skill、Agent、Tool 作为平等候选交给 LLM 判断。
唯一的确定性快速路径：已有 artifact 时的编辑检测。
ask_user 续接：上一轮有 ask_user 工具调用时直接返回同一执行器。
"""

from __future__ import annotations

import json
import re

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from lea_graph.state import PlatformState

logger = structlog.get_logger()

_ROUTE_PROMPT = """\
你是意图路由器。根据用户消息选择最合适的处理方式。

可用候选（name: description）：
{candidates}

规则：
1. 选择最匹配用户意图的候选
2. 如果没有明确匹配，选 fallback（通用对话）
3. 只输出 JSON，无其他内容

输出格式：
{{"name": "候选名称"}}"""

# --- 确定性快速路径 ---

_SLIDE_EDIT_KEYWORDS = re.compile(
    r"(第.{1,3}页|封面|最后一页|标题改|加.{0,4}要点|删.{0,2}页|插入.{0,4}页|"
    r"换个.{0,4}布局|改.{0,4}配色|修改.{0,4}(内容|文字|图标)|下载.{0,2}(PPT|ppt|文稿))"
)


def _is_slide_edit_intent(text: str) -> bool:
    return bool(_SLIDE_EDIT_KEYWORDS.search(text))


def _extract_user_text(state: PlatformState) -> str:
    """从状态中提取最后一条用户消息用于路由。"""
    user_messages = [
        msg.content for msg in state["messages"]
        if isinstance(msg, HumanMessage)
    ]
    return user_messages[-1] if user_messages else ""


def _has_pending_ask_user(state: PlatformState) -> bool:
    """检测上一轮是否调用了 ask_user 工具。

    如果是，用户当前消息是对追问的回复，应该跳过路由直接续接。
    """
    messages = state.get("messages", [])
    if len(messages) < 2:
        return False

    # 从倒数第二条消息开始往前找（最后一条是当前用户消息）
    for msg in reversed(messages[:-1]):
        if isinstance(msg, HumanMessage):
            break
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                if tc.get("name") == "ask_user":
                    return True
        if isinstance(msg, ToolMessage):
            if "已向用户提问" in (msg.content or ""):
                return True

    return False


def _get_ask_user_question(state: PlatformState) -> str:
    """获取上一轮 ask_user 的问题内容。"""
    messages = state.get("messages", [])
    for msg in reversed(messages[:-1]):
        if isinstance(msg, HumanMessage):
            break
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                if tc.get("name") == "ask_user":
                    return tc.get("args", {}).get("question", "")
    return ""


async def _is_reply_to_question(question: str, user_reply: str) -> bool:
    """判断用户消息是否在回答之前的追问（而非切换新话题）。"""
    from lea_graph.llm import get_chat_model

    prompt = (
        "判断用户的回复是否在回答上面的问题，还是切换了新话题。\n"
        "只输出 reply 或 new_topic，无其他内容。"
    )
    model = get_chat_model("routing", streaming=False, temperature=0)
    resp = await model.ainvoke([
        SystemMessage(content=prompt),
        HumanMessage(content=f"问题：{question}\n\n用户回复：{user_reply}"),
    ])
    content = resp.content.strip().lower()
    if "</think>" in content:
        content = content[content.rfind("</think>") + 8:].strip()
    return "reply" in content


# --- 统一候选列表构建 ---

def _build_candidates(configurable: dict) -> list[dict]:
    """将 Skill、Agent 汇总为统一候选列表。"""
    candidates = []

    # Skills (from DB)
    for item in configurable.get("skill_candidates", []):
        candidates.append({
            "name": item["name"],
            "display_name": item.get("display_name") or item["name"],
            "type": "skill",
            "description": item.get("description", ""),
        })

    # Agents (from App registry)
    for app_name, agents in configurable.get("app_skills", {}).items():
        for agent_name in agents:
            candidates.append({
                "name": f"{app_name}/{agent_name}",
                "display_name": f"{app_name}/{agent_name}",
                "type": "agent",
                "app": app_name,
                "agent": agent_name,
                "description": f"{app_name} 应用的 {agent_name} 智能体",
            })

    # Fallback 总是兜底
    candidates.append({
        "name": "fallback",
        "type": "fallback",
        "description": "通用对话问答，当其他候选都不匹配时使用",
    })

    # Teams
    for team_info in configurable.get("team_candidates", []):
        candidates.append({
            "name": f"team:{team_info['name']}",
            "display_name": team_info.get("display_name") or team_info["name"],
            "type": "team",
            "description": team_info.get("description", ""),
        })

    return candidates


# --- LLM 路由 ---

async def _llm_route(text: str, candidates: list[dict]) -> dict:
    """LLM 从统一候选列表中选择最合适的。"""
    from lea_graph.llm import get_chat_model

    candidate_str = "\n".join(
        f"- {c['name']}: {c['description']}" for c in candidates
    )
    prompt = _ROUTE_PROMPT.format(candidates=candidate_str)

    model = get_chat_model("routing", streaming=False, temperature=0)
    response = await model.ainvoke([
        SystemMessage(content=prompt),
        HumanMessage(content=text),
    ])

    try:
        content = response.content.strip()
        if "</think>" in content:
            content = content[content.rfind("</think>") + 8:].strip()
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(content[start:end])
    except (json.JSONDecodeError, KeyError):
        pass

    return {"name": "fallback"}


# --- 路由节点 ---

async def route_message(state: PlatformState, config: RunnableConfig) -> dict:
    """路由节点：分析意图，选择最合适的 Skill/Agent。

    策略：预设快捷路径 → ask_user 续接 → Artifact 编辑检测 → LLM 统一语义路由 → Fallback。
    """
    writer = get_stream_writer()

    # 快捷路径：chat.py 已预设 target_app（如 Team 直接选用），跳过 LLM 路由
    if state.get("target_app") and state["target_app"] != "":
        pre = state["target_app"]
        logger.info("intent_preset", target_app=pre)
        writer({"type": "intent", "app": pre, "skill_display_name": pre, "confidence": 1.0})
        return {}

    text = _extract_user_text(state)

    # -1. ask_user 续接检测 — 上一轮追问了用户，判断是回答还是新话题
    if _has_pending_ask_user(state):
        question = _get_ask_user_question(state)
        is_reply = await _is_reply_to_question(question, text) if question else True
        if is_reply:
            logger.info("intent_continue_ask_user")
            writer({"type": "thinking", "content": "继续处理您的回复..."})
            writer({"type": "intent", "app": "fallback", "agent": "qa",
                    "skill_display_name": "续接回复", "confidence": 1.0})
            return {"target_app": "fallback", "target_agent": "qa", "target_skill": ""}
        else:
            logger.info("intent_new_topic_after_ask_user", text=text[:50])

    writer({"type": "thinking", "content": "正在分析您的意图..."})

    configurable = config.get("configurable", {})

    # 0. Artifact 编辑检测 — session 已有 slides 且消息是修改类
    artifact_id = state.get("session_metadata", {}).get("slides_artifact_id")
    if artifact_id and _is_slide_edit_intent(text):
        logger.info("intent_slide_edit", artifact_id=artifact_id)
        writer({"type": "intent", "skill": "slide-editor",
                "skill_display_name": "演示文稿编辑", "confidence": 0.95})
        return {"target_app": "skill", "target_agent": "", "target_skill": "slide-editor"}

    # 1. LLM 统一语义路由
    candidates = _build_candidates(configurable)
    logger.info("route_candidates", count=len(candidates), text=text[:50],
                names=[c["name"] for c in candidates])
    try:
        result = await _llm_route(text, candidates)
        chosen_name = result.get("name", "fallback")
    except Exception as e:
        logger.warning("llm_route_failed", error=str(e))
        chosen_name = "fallback"

    # 2. 解析路由结果
    chosen = next((c for c in candidates if c["name"] == chosen_name), None)

    def _display(cand: dict | None, fallback: str) -> str:
        return (cand or {}).get("display_name") or fallback

    if chosen and chosen["type"] == "skill":
        logger.info("intent_routed", target=chosen_name, type="skill")
        writer({"type": "intent", "skill": chosen_name,
                "skill_display_name": _display(chosen, chosen_name), "confidence": 0.9})
        return {"target_app": "skill", "target_agent": "", "target_skill": chosen_name}

    if chosen and chosen["type"] == "agent":
        app_name = chosen["app"]
        agent_name = chosen["agent"]
        logger.info("intent_routed", target=chosen_name, type="agent")
        writer({"type": "intent", "app": app_name, "agent": agent_name,
                "skill_display_name": _display(chosen, f"{app_name}/{agent_name}"),
                "confidence": 0.9})
        return {"target_app": app_name, "target_agent": agent_name, "target_skill": ""}

    if chosen and chosen["type"] == "team":
        team_name = chosen_name.removeprefix("team:")
        logger.info("intent_routed", target=chosen_name, type="team")
        writer({"type": "intent", "team": team_name,
                "skill_display_name": _display(chosen, team_name), "confidence": 0.9})
        return {"target_app": "team", "target_agent": "", "target_skill": ""}

    # 3. Fallback
    logger.info("intent_routed", target="fallback_qa", type="fallback")
    writer({"type": "intent", "app": "fallback", "agent": "qa",
            "skill_display_name": "智能问答", "confidence": 0.5})
    return {"target_app": "fallback", "target_agent": "qa", "target_skill": ""}


def pick_agent(state: PlatformState) -> str:
    """条件边函数：根据 target 选择下一个节点。"""
    app = state.get("target_app", "fallback")
    agent = state.get("target_agent", "qa")

    if app == "team":
        return "team_executor"
    if app == "skill":
        return "skill_executor"
    if app == "fallback":
        return "fallback_qa"
    return f"{app}__{agent}"
