"""用户画像管理 — session 结束时增量 merge。"""

from __future__ import annotations

import re

import structlog

from lea_rag.db import SessionLocal
from lea_rag.db.models import UserMemory, UserProfile

logger = structlog.get_logger()

_MERGE_PROMPT = """\
你是用户画像管理器。根据旧画像和新提取的事实，生成更新后的用户画像。

规则：
- 保持简洁，不超过 200 字
- 用条目形式（每条一行），不要写段落
- 新信息与旧信息矛盾时，以新信息为准
- 删除过时信息，保留仍然有效的
- 只保留跨对话有价值的信息（身份/偏好/背景/长期目标）

旧画像：
{old_profile}

新增事实：
{new_facts}

输出更新后的完整画像（纯文本，条目式）："""


async def get_profile(user_id: str, agent_id: str = "", tenant_id: str = "default") -> str:
    db = SessionLocal()
    try:
        row = db.query(UserProfile).filter_by(
            user_id=user_id, agent_id=agent_id, tenant_id=tenant_id
        ).first()
        return row.profile_text if row else ""
    finally:
        db.close()


async def merge_profile(
    user_id: str,
    agent_id: str = "",
    tenant_id: str = "default",
    session_id: str = "",
) -> str:
    """从本次 session 新增的记忆中 merge 画像。仅在有新记忆时调用 LLM。"""
    from lea_rag.llm import get_chat_model
    from lea_rag.memory.config import get_memory_settings

    db = SessionLocal()
    try:
        # 取本次 session 新增的记忆，按 scope 过滤
        q = db.query(UserMemory).filter(
            UserMemory.user_id == user_id,
            UserMemory.tenant_id == tenant_id,
        )
        if session_id:
            q = q.filter(UserMemory.source_session_id == session_id)

        # 全局画像读 scope="global" 的记忆，agent 画像读对应 agent 的
        if agent_id:
            q = q.filter(UserMemory.scope == f"agent:{agent_id}")
        else:
            q = q.filter(UserMemory.scope == "global")

        new_memories = q.all()

        if not new_memories:
            return ""

        # 取旧画像
        row = db.query(UserProfile).filter_by(
            user_id=user_id, agent_id=agent_id, tenant_id=tenant_id
        ).first()
        old_profile = row.profile_text if row else "（暂无画像）"

        new_facts = "\n".join(f"- {m.content}" for m in new_memories)

        settings = get_memory_settings()
        model = get_chat_model(
            settings.extract_model or "routing",
            streaming=False, temperature=0,
        )
        resp = await model.ainvoke([{
            "role": "user",
            "content": _MERGE_PROMPT.format(
                old_profile=old_profile, new_facts=new_facts
            ),
        }])

        text = resp.content.strip()
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

        if not text:
            return old_profile

        # 更新或创建画像
        if row:
            row.profile_text = text
            row.fact_count = len(new_memories)
            row.version += 1
        else:
            row = UserProfile(
                user_id=user_id, agent_id=agent_id, tenant_id=tenant_id,
                profile_text=text, fact_count=len(new_memories),
            )
            db.add(row)

        db.commit()
        logger.info("profile_merged", user_id=user_id, agent_id=agent_id,
                    facts=len(new_memories), version=row.version)
        return text

    finally:
        db.close()
