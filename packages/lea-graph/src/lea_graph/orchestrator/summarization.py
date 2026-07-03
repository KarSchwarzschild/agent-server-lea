"""对话摘要节点 — 基于 langmem SummarizationNode。

当消息 token 数超过阈值时，自动将旧消息压缩为摘要，
保留最近的消息窗口，摘要存入 state["context"]。
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger()


def _approx_token_count(messages) -> int:
    """近似 token 计数器，适用于中文为主的对话。

    中文平均 ~1.5 字符/token，英文 ~4 字符/token。
    取保守估计：2 字符/token。
    """
    total = 0
    for msg in messages:
        content = ""
        if hasattr(msg, "content"):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
        elif isinstance(msg, dict):
            content = msg.get("content", "")
        total += len(content) // 2 + 4  # +4 for role/metadata overhead
    return total


def build_summarization_node():
    """构建 SummarizationNode 实例。

    使用 routing 模型（较便宜）做摘要，限制摘要输出长度。
    """
    from langmem.short_term import SummarizationNode

    from lea_graph.llm import get_chat_model

    summarization_model = get_chat_model(
        "routing", streaming=False, max_tokens=500, with_fallback=False
    )

    node = SummarizationNode(
        token_counter=_approx_token_count,
        model=summarization_model,
        max_tokens=4000,
        max_tokens_before_summary=6000,
        max_summary_tokens=500,
    )

    logger.info("summarization_node_built", max_tokens=4000, threshold=6000)
    return node
