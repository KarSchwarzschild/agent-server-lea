"""
RAG 质量评测 — context precision/recall, faithfulness, hallucination.

参考 rnp 项目的评测框架，使用 LLM-as-Judge 评估 RAG 输出质量。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from lea_graph.llm import get_chat_model

logger = structlog.get_logger()

CONTEXT_PRECISION_PROMPT = """评估检索到的上下文对回答问题的精确度。

问题：{query}
检索到的上下文：
{context}

请评估上下文中有多少比例的内容与问题相关。
回复JSON：{{"score": <0.0-1.0>, "reason": "<理由>"}}"""

FAITHFULNESS_PROMPT = """评估AI回答是否忠实于给定的上下文（不编造信息）。

问题：{query}
上下文：{context}
AI回答：{response}

请评估回答中的每个声明是否都能在上下文中找到支持。
回复JSON：{{"score": <0.0-1.0>, "reason": "<理由>", "unsupported_claims": [<不被支持的声明>]}}"""

HALLUCINATION_PROMPT = """检测AI回答中的幻觉内容（编造的事实、错误的引用等）。

问题：{query}
上下文：{context}
AI回答：{response}

请找出回答中所有无法从上下文验证的声明。
回复JSON：{{"score": <0.0-1.0>, "hallucinations": [<幻觉内容>], "reason": "<理由>"}}"""


@dataclass
class RAGEvalResult:
    context_precision: float = 0.0
    faithfulness: float = 0.0
    hallucination_score: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)


class RAGJudge:
    """RAG 质量评测器。"""

    def __init__(self, purpose: str = "routing"):
        self._llm = get_chat_model(purpose=purpose, temperature=0.0)

    async def evaluate(
        self,
        query: str,
        context: str,
        response: str,
    ) -> RAGEvalResult:
        """评测单个 RAG 输出的质量。"""
        cp = await self._eval_with_prompt(
            CONTEXT_PRECISION_PROMPT.format(query=query, context=context)
        )
        faith = await self._eval_with_prompt(
            FAITHFULNESS_PROMPT.format(query=query, context=context, response=response)
        )
        hall = await self._eval_with_prompt(
            HALLUCINATION_PROMPT.format(query=query, context=context, response=response)
        )

        return RAGEvalResult(
            context_precision=cp.get("score", 0.0),
            faithfulness=faith.get("score", 0.0),
            hallucination_score=hall.get("score", 0.0),
            details={
                "context_precision": cp,
                "faithfulness": faith,
                "hallucination": hall,
            },
        )

    async def _eval_with_prompt(self, prompt: str) -> dict[str, Any]:
        try:
            result = await self._llm.ainvoke([HumanMessage(content=prompt)])
            content = result.content
            start = content.index("{")
            end = content.rindex("}") + 1
            return json.loads(content[start:end])
        except Exception as e:
            logger.error("rag_judge_error", error=str(e))
            return {"score": 0.0, "reason": str(e)}
