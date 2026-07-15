"""记忆图节点 — context_inject + memory_extract 的增强实现。

替换原 memory_nodes.py 的功能：
- context_inject: 基于当前 query 语义检索相关记忆（而非全量加载最近 20 条）
- memory_extract: 用增强版提取器（带 category/importance + 向量去重）
"""

from __future__ import annotations

from typing import Any

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from lea_graph.memory.config import get_memory_settings
from lea_graph.state import PlatformState

logger = structlog.get_logger()


def build_messages_with_memories(messages: list, memories: list[str]) -> list:
    """将长期记忆注入到消息列表中供 LLM 使用。"""
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
    """加载用户记忆（语义检索），注入到 state。递增 turn_count。"""
    meta = state.get("session_metadata") or {}
    turn_count = meta.get("turn_count", 0) + 1
    new_meta = {"turn_count": turn_count}

    user_id = state.get("user_id", "")
    result: dict[str, Any] = {"session_metadata": new_meta}

    messages = state.get("messages", [])
    session_id = meta.get("db_session_id", "")
    if len(messages) <= 1 and session_id:
        history = _load_history_from_db(session_id)
        if history:
            result["messages"] = history
            logger.info("history_restored", count=len(history), session_id=session_id)

    if not user_id:
        return result

    # 语义检索：用当前 query 找相关记忆
    query = _extract_user_text(state)
    if query:
        try:
            from lea_graph.memory.retriever import MemoryRetriever

            configurable = config.get("configurable", {})
            tenant_id = configurable.get("tenant_id", "default")
            retriever = MemoryRetriever()
            memories = await retriever.search(user_id, query, top_k=10, tenant_id=tenant_id)
            memory_texts = [m.content for m in memories]
            if memory_texts:
                logger.info("memories_injected", count=len(memory_texts), user_id=user_id)
                result["user_memories"] = memory_texts
                return result
        except Exception as e:
            logger.warning("memory_semantic_search_failed", error=str(e))

    # fallback: 从 MySQL 加载最近记忆
    memories = _load_memories_from_db(user_id)
    if memories:
        logger.info("memories_loaded_fallback", count=len(memories), user_id=user_id)
    result["user_memories"] = memories
    return result


async def memory_extract_node(
    state: PlatformState, config: RunnableConfig
) -> dict[str, Any]:
    """对话结束后提取用户长期记忆。"""
    settings = get_memory_settings()
    messages = state.get("messages", [])
    if len(messages) < settings.extract_min_messages:
        return {}

    meta = state.get("session_metadata") or {}
    turn_count = meta.get("turn_count", 0)
    if turn_count % settings.extract_interval != 0:
        return {}

    user_id = state.get("user_id", "")
    if not user_id:
        return {}

    configurable = config.get("configurable", {})
    tenant_id = configurable.get("tenant_id", "default")
    session_id = meta.get("db_session_id", "")

    try:
        from lea_graph.memory.extractor import extract_memories
        await extract_memories(messages, user_id, tenant_id, session_id)
    except Exception as e:
        logger.warning("memory_extract_failed", error=str(e))

    return {}


# --- 辅助函数 ---

def _extract_user_text(state: PlatformState) -> str:
    messages = state.get("messages", [])
    user_messages = [
        msg.content for msg in messages
        if isinstance(msg, HumanMessage)
    ]
    return user_messages[-1] if user_messages else ""


def _load_memories_from_db(user_id: str) -> list[str]:
    try:
        from lea_server.database import SessionLocal
        from lea_server.models.chat_session import UserMemory

        db = SessionLocal()
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
    try:
        from lea_server.database import SessionLocal
        from lea_server.models.chat_session import ChatMessage

        db = SessionLocal()
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
