"""LLM 记忆提取器 — 从对话中提取结构化用户 fact。

增强版：带 category + importance 分类，向量 cosine 去重。
"""

from __future__ import annotations

import json
import re

import structlog
from langchain_core.messages import AIMessage, HumanMessage

from lea_rag.memory.config import get_memory_settings

logger = structlog.get_logger()

_EXTRACT_PROMPT = """\
从以下对话中提取用户的持久性信息。只提取跨对话有价值的事实。

输出 JSON 数组，每条包含：
- content: 事实内容（简洁一句话）
- category: 分类（fact/preference/identity/progress）
- importance: 重要度 1-10
- scope: "profile" 或 "context"

scope 判断规则：
- profile: 用户身份、背景、偏好、通用特征（所有场景都适用）
  例: "用户是大二学生"、"偏好简洁风格"、"在北京工作"
- context: 当前任务/对话的具体进展（只对当前 agent 有意义）
  例: "正在用微积分解优化问题"、"下次要验证方案B"、"已完成第三章"

如无值得记忆的信息，输出 []

输出格式示例：
[{"content": "用户是大二数学老师", "category": "identity", "importance": 8, "scope": "profile"},
 {"content": "正在准备期末考试出题", "category": "progress", "importance": 6, "scope": "context"}]"""


async def extract_memories(
    messages: list, user_id: str, tenant_id: str = "default",
    session_id: str = "", agent_id: str = "",
) -> list[dict]:
    """从对话中提取记忆，去重后返回新增项。支持双层 scope。"""
    from lea_rag.llm import get_chat_model
    from lea_rag.memory.service import MemoryService

    settings = get_memory_settings()

    recent = messages[-20:]
    conversation = []
    for msg in recent:
        if isinstance(msg, HumanMessage):
            conversation.append(f"用户: {msg.content[:300]}")
        elif isinstance(msg, AIMessage) and msg.content:
            conversation.append(f"助手: {msg.content[:200]}")

    if not conversation:
        return []

    model = get_chat_model(
        settings.extract_model or "routing",
        streaming=False, temperature=0,
    )
    resp = await model.ainvoke([
        {"role": "system", "content": _EXTRACT_PROMPT},
        {"role": "user", "content": "\n".join(conversation)},
    ])

    text = resp.content.strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    facts = _parse_facts(text)
    if not facts:
        return []

    svc = MemoryService()
    existing = await svc.get_all(user_id, tenant_id)
    new_facts = await _dedup_by_embedding(facts, existing, svc, settings.dedup_threshold)

    added = []
    for fact in new_facts:
        # scope 判断：profile → global（全平台共享），context → agent 专属
        fact_scope = fact.get("scope", "context")
        if fact_scope == "profile":
            scope = "global"
        elif agent_id:
            scope = f"agent:{agent_id}"
        else:
            scope = "global"

        mid = await svc.add(
            user_id, fact["content"],
            category=fact.get("category", "fact"),
            importance=fact.get("importance", 5),
            tenant_id=tenant_id,
            source_session_id=session_id,
            scope=scope,
        )
        added.append({**fact, "id": mid, "scope": scope})

    if added:
        logger.info("memories_extracted", count=len(added), user_id=user_id)
    return added


def _parse_facts(text: str) -> list[dict]:
    """解析 LLM 输出的 JSON 数组。"""
    try:
        start = text.find("[")
        end = text.rfind("]") + 1
        if start >= 0 and end > start:
            items = json.loads(text[start:end])
            if isinstance(items, list):
                result = []
                for item in items:
                    if isinstance(item, str):
                        result.append({"content": item, "category": "fact", "importance": 5, "scope": "context"})
                    elif isinstance(item, dict) and item.get("content"):
                        result.append({
                            "content": item["content"],
                            "category": item.get("category", "fact"),
                            "importance": min(max(int(item.get("importance", 5)), 1), 10),
                            "scope": item.get("scope", "context"),
                        })
                return result
    except (json.JSONDecodeError, ValueError):
        pass
    return []


async def _dedup_by_embedding(
    new_facts: list[dict],
    existing: list,
    svc,
    threshold: float,
) -> list[dict]:
    """向量余弦相似度去重：cosine > threshold 视为重复。"""
    if not existing:
        return new_facts

    try:
        new_texts = [f["content"] for f in new_facts]
        existing_texts = [m.content for m in existing]

        all_texts = new_texts + existing_texts
        all_vecs = await svc._get_embeddings_batch(all_texts)

        new_vecs = all_vecs[:len(new_texts)]
        existing_vecs = all_vecs[len(new_texts):]

        unique = []
        for i, nvec in enumerate(new_vecs):
            is_dup = False
            for evec in existing_vecs:
                sim = _cosine_sim(nvec, evec)
                if sim > threshold:
                    is_dup = True
                    break
            if not is_dup:
                unique.append(new_facts[i])
        return unique
    except Exception as e:
        logger.warning("dedup_embedding_failed_fallback_text", error=str(e))
        return _dedup_by_text(new_facts, [m.content for m in existing])


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """计算余弦相似度。"""
    import math
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _dedup_by_text(new_facts: list[dict], existing_texts: list[str]) -> list[dict]:
    """文本 fallback 去重（子串+bigram jaccard）。"""
    existing_set = {t.strip().lower() for t in existing_texts if t}
    unique = []
    for fact in new_facts:
        content = fact["content"].strip().lower()
        is_dup = False
        for ex in existing_set:
            if content in ex or ex in content:
                is_dup = True
                break
            if len(content) > 4 and len(ex) > 4:
                s1 = {content[i:i+2] for i in range(len(content) - 1)}
                s2 = {ex[i:i+2] for i in range(len(ex) - 1)}
                if len(s1 & s2) / max(len(s1 | s2), 1) > 0.7:
                    is_dup = True
                    break
        if not is_dup:
            unique.append(fact)
    return unique
