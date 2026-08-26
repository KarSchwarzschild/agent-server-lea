"""
LLM-as-Judge — 通用 LLM 评分器。

让 LLM 对 Agent 输出打分（1-5），并给出评分理由。
支持自定义评分 prompt 和评分维度。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from lea_graph.llm import get_chat_model

logger = structlog.get_logger()

JUDGE_SYSTEM_PROMPT = """你是一个AI输出质量评估专家。请对以下AI输出进行评分。

评分标准（1-5分）：
1分：完全不相关或错误
2分：部分相关但有明显问题
3分：基本正确但不够深入
4分：正确且有深度
5分：优秀，准确、深入、结构清晰

请严格按照JSON格式回复：
{"score": <1-5>, "reason": "<评分理由>", "dimensions": {"relevance": <1-5>, "accuracy": <1-5>, "completeness": <1-5>}}
"""


@dataclass
class JudgeResult:
    score: float
    reason: str
    dimensions: dict[str, float]
    raw_response: str = ""


class LLMJudge:
    """LLM-as-Judge 评分器。"""

    def __init__(self, purpose: str = "routing"):
        self._llm = get_chat_model(purpose=purpose, temperature=0.0)

    async def evaluate(
        self,
        query: str,
        response: str,
        reference: str | None = None,
        custom_prompt: str | None = None,
    ) -> JudgeResult:
        """评估 AI 输出质量。"""
        system = custom_prompt or JUDGE_SYSTEM_PROMPT

        user_content = f"用户问题：{query}\n\nAI回答：{response}"
        if reference:
            user_content += f"\n\n参考答案：{reference}"

        messages = [SystemMessage(content=system), HumanMessage(content=user_content)]

        try:
            result = await self._llm.ainvoke(messages)
            return self._parse_result(result.content)
        except Exception as e:
            logger.error("llm_judge_error", error=str(e))
            return JudgeResult(score=0, reason=f"Judge error: {e}", dimensions={})

    def _parse_result(self, content: str) -> JudgeResult:
        try:
            start = content.index("{")
            end = content.rindex("}") + 1
            data = json.loads(content[start:end])
            return JudgeResult(
                score=float(data.get("score", 0)),
                reason=data.get("reason", ""),
                dimensions=data.get("dimensions", {}),
                raw_response=content,
            )
        except (json.JSONDecodeError, ValueError):
            return JudgeResult(score=0, reason=f"Parse error: {content[:200]}", dimensions={}, raw_response=content)
