"""RAG 质量评测 — LLM-as-Judge 评分器。"""

from __future__ import annotations

import json

import structlog
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from lea_rag.llm import get_chat_model
from lea_rag.evaluation.metrics import RAGMetrics

logger = structlog.get_logger(__name__)

_FAITHFULNESS_PROMPT = """你是一个严格的事实核查员。请判断答案中的每个声明是否可以从给定的上下文中找到支持。

上下文:
{context}

答案:
{answer}

请逐条列出答案中的事实声明，并判断每条是否有上下文支持。
以 JSON 格式输出: {{"claims": [{{"claim": "...", "supported": true/false}}]}}
只输出 JSON，不要其他文字。"""

_HALLUCINATION_PROMPT = """你是一个幻觉检测专家。判断答案中是否包含上下文中未提及的虚构内容。

上下文:
{context}

问题:
{question}

答案:
{answer}

请以 JSON 输出: {{"hallucinated_claims": ["..."], "total_claims": N, "hallucination_count": N}}
只输出 JSON。"""

_RELEVANCE_PROMPT = """评估答案对问题的相关性和完整性，1-5分。

问题: {question}
答案: {answer}

输出 JSON: {{"score": N, "reason": "..."}}
只输出 JSON。"""


class RAGJudge:
    """RAG 质量评测器 — 通过 LLM 评估 faithfulness / hallucination / relevance。"""

    def __init__(self, llm: BaseChatModel | None = None):
        self._llm = llm or get_chat_model()

    async def evaluate(
        self,
        question: str,
        answer: str,
        contexts: list[str],
    ) -> RAGMetrics:
        context_str = "\n---\n".join(contexts)

        faithfulness, hallucination, relevance = 0.0, 0.0, 0.0
        try:
            faithfulness = await self._eval_faithfulness(context_str, answer)
        except Exception:
            logger.exception("faithfulness_eval_failed")
        try:
            hallucination = await self._eval_hallucination(context_str, question, answer)
        except Exception:
            logger.exception("hallucination_eval_failed")
        try:
            relevance = await self._eval_relevance(question, answer)
        except Exception:
            logger.exception("relevance_eval_failed")

        return RAGMetrics(
            faithfulness=faithfulness,
            hallucination_rate=hallucination,
            answer_relevance=relevance,
        )

    async def _eval_faithfulness(self, context: str, answer: str) -> float:
        resp = await self._llm.ainvoke([
            SystemMessage(content="你是一个事实核查员。"),
            HumanMessage(content=_FAITHFULNESS_PROMPT.format(context=context, answer=answer)),
        ])
        data = _parse_json(resp.content)
        claims = data.get("claims", [])
        if not claims:
            return 0.0
        supported = sum(1 for c in claims if c.get("supported"))
        return supported / len(claims)

    async def _eval_hallucination(self, context: str, question: str, answer: str) -> float:
        resp = await self._llm.ainvoke([
            SystemMessage(content="你是一个幻觉检测专家。"),
            HumanMessage(content=_HALLUCINATION_PROMPT.format(
                context=context, question=question, answer=answer
            )),
        ])
        data = _parse_json(resp.content)
        total = data.get("total_claims", 1)
        hallucinated = data.get("hallucination_count", 0)
        return hallucinated / max(total, 1)

    async def _eval_relevance(self, question: str, answer: str) -> float:
        resp = await self._llm.ainvoke([
            SystemMessage(content="你是一个答案质量评估专家。"),
            HumanMessage(content=_RELEVANCE_PROMPT.format(question=question, answer=answer)),
        ])
        data = _parse_json(resp.content)
        return data.get("score", 0) / 5.0


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("json_parse_failed", text=text[:200])
        return {}
