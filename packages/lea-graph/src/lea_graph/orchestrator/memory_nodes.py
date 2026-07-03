"""上下文注入 + 长期记忆提取节点。

长期记忆持久化到 MySQL（user_memories 表），Redis 不再承担记忆存储。
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from lea_graph.state import PlatformState

logger = structlog.get_logger()

_EXTRACT_PROMPT = """\
从以下对话中提取用户的持久性信息（偏好、背景、身份、习惯等）。
只提取跨对话有价值的事实，忽略一次性请求细节。
输出 JSON 数组: ["事实1", "事实2", ...]
如果没有值得记忆的信息，输出 []"""


def _get_db_session():
    """获取数据库 session（同步）。"""
    from lea_server.database import SessionLocal
    return SessionLocal()


def build_messages_with_memories(messages: list, memories: list[str]) -> list:
    """公共方法：将长期记忆注入到消息列表中供 LLM 使用。"""
    if not memories:
        return list(messages)

    memory_text = "\n".join(f"- {m}" for m in memories)
    sys_msg = SystemMessage(
        content=f"你已知道关于此用户的信息：\n{memory_text}\n\n"
        "请在回答时自然地利用这些信息。"
    )
    return [sys_msg] + list(messages)


async def context_inject_node(
    state: PlatformState, config: RunnableConfig
) -> dict[str, Any]:
    """加载用户长期记忆（MySQL），注入到 state。递增 turn_count。

    如果 checkpoint 过期（messages 只有当前 1 条），从 MySQL 恢复历史。
    """
    meta = state.get("session_metadata") or {}
    turn_count = meta.get("turn_count", 0) + 1
    new_meta = {"turn_count": turn_count}

    user_id = state.get("user_id", "")
    result: dict[str, Any] = {"session_metadata": new_meta}

    # checkpoint 过期兜底：messages <= 1 说明没有历史上下文
    messages = state.get("messages", [])
    session_id = meta.get("db_session_id", "")
    if len(messages) <= 1 and session_id:
        history = _load_history_from_db(session_id)
        if history:
            result["messages"] = history
            logger.info("history_restored", count=len(history), session_id=session_id)

    if not user_id:
        return result

    memories = _load_memories_from_db(user_id)
    if memories:
        logger.info("memories_loaded", count=len(memories), user_id=user_id)

    result["user_memories"] = memories
    return result


async def memory_extract_node(
    state: PlatformState, config: RunnableConfig
) -> dict[str, Any]:
    """对话结束后提取用户长期记忆并存入 MySQL。"""
    messages = state.get("messages", [])
    if len(messages) < 6:
        return {}

    meta = state.get("session_metadata") or {}
    turn_count = meta.get("turn_count", 0)
    if turn_count % 5 != 0:
        return {}

    user_id = state.get("user_id", "")
    if not user_id:
        return {}

    try:
        facts = await _extract_user_facts(messages)
    except Exception as e:
        logger.warning("memory_extract_failed", error=str(e))
        return {}

    if not facts:
        return {}

    existing = set(_load_memories_from_db(user_id))
    new_facts = [f for f in facts if not _is_duplicate(f, existing)]

    if not new_facts:
        logger.debug("memories_all_duplicates", user_id=user_id)
        return {}

    _save_memories_to_db(user_id, new_facts)
    logger.info("memories_saved", count=len(new_facts), user_id=user_id)
    return {}


# --- MySQL 读写 ---

def _load_memories_from_db(user_id: str) -> list[str]:
    """从 MySQL 加载用户的所有长期记忆。"""
    try:
        from lea_server.models.chat_session import UserMemory
        db = _get_db_session()
        rows = (
            db.query(UserMemory)
            .filter(UserMemory.user_id == user_id)
            .order_by(UserMemory.created_at.desc())
            .limit(20)
            .all()
        )
        result = [r.content for r in rows if r.content]
        db.close()
        return result
    except Exception as e:
        logger.warning("memory_db_load_failed", error=str(e))
        return []


def _load_history_from_db(session_id: str) -> list:
    """从 MySQL 加载最近 10 轮对话历史（checkpoint 过期兜底）。"""
    try:
        from lea_server.models.chat_session import ChatMessage
        db = _get_db_session()
        rows = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(20)
            .all()
        )
        db.close()

        messages = []
        for msg in reversed(rows):
            if msg.role == "user":
                messages.append(HumanMessage(content=msg.content))
            elif msg.role == "assistant":
                messages.append(AIMessage(content=msg.content))
        return messages
    except Exception as e:
        logger.warning("history_db_load_failed", error=str(e))
        return []


def _save_memories_to_db(user_id: str, facts: list[str]) -> None:
    """写入新的记忆到 MySQL。"""
    try:
        from lea_server.models.chat_session import UserMemory
        db = _get_db_session()
        for fact in facts:
            db.add(UserMemory(user_id=user_id, content=fact))
        db.commit()
        db.close()
    except Exception as e:
        logger.warning("memory_db_save_failed", error=str(e))


# --- 去重 ---

def _is_duplicate(new_fact: str, existing_texts: set[str]) -> bool:
    """简单去重：子串包含或 bigram jaccard > 0.7 视为重复。"""
    new_normalized = new_fact.strip().lower()
    for existing in existing_texts:
        existing_normalized = existing.strip().lower()
        if not existing_normalized:
            continue
        if new_normalized in existing_normalized or existing_normalized in new_normalized:
            return True
        if len(new_normalized) > 4 and len(existing_normalized) > 4:
            s1 = {new_normalized[i:i+2] for i in range(len(new_normalized) - 1)}
            s2 = {existing_normalized[i:i+2] for i in range(len(existing_normalized) - 1)}
            overlap = len(s1 & s2) / max(len(s1 | s2), 1)
            if overlap > 0.7:
                return True
    return False


# --- LLM 提取 ---

async def _extract_user_facts(messages: list) -> list[str]:
    """用 LLM 从对话中提取用户事实。"""
    import re

    from lea_graph.llm import get_chat_model

    recent = messages[-20:]
    conversation = []
    for msg in recent:
        if isinstance(msg, HumanMessage):
            conversation.append(f"用户: {msg.content[:300]}")
        elif isinstance(msg, AIMessage) and msg.content:
            conversation.append(f"助手: {msg.content[:200]}")

    if not conversation:
        return []

    model = get_chat_model("routing", streaming=False, temperature=0)
    resp = await model.ainvoke([
        {"role": "system", "content": _EXTRACT_PROMPT},
        {"role": "user", "content": "\n".join(conversation)},
    ])

    text = resp.content.strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    try:
        start = text.find("[")
        end = text.rfind("]") + 1
        if start >= 0 and end > start:
            facts = json.loads(text[start:end])
            if isinstance(facts, list):
                return [f for f in facts if isinstance(f, str) and f.strip()]
    except (json.JSONDecodeError, ValueError):
        pass
    return []
