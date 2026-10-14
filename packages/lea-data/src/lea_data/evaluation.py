"""自动质量评测 — 差评触发 LLM-as-Judge 评分。

数据飞轮核心环节：
  Kafka lea.feedback (dislike) → AutoEvaluator.evaluate()
    → LLM 多维评分 (relevance, accuracy, helpfulness)
    → 低分样本标记 → 改进建议生成
    → 结果写回 feedback 表 + 发送 lea.metrics 事件
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import structlog
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from lea_rag.llm import get_chat_model

logger = structlog.get_logger(__name__)


@dataclass
class EvalDimension:
    name: str
    score: float
    reason: str = ""


@dataclass
class AutoEvalResult:
    message_id: str
    overall_score: float = 0.0
    dimensions: list[EvalDimension] = field(default_factory=list)
    improvement_suggestion: str = ""
    needs_attention: bool = False

    def to_dict(self) -> dict:
        return {
            "message_id": self.message_id,
            "overall_score": round(self.overall_score, 2),
            "dimensions": [
                {"name": d.name, "score": round(d.score, 2), "reason": d.reason}
                for d in self.dimensions
            ],
            "improvement_suggestion": self.improvement_suggestion,
            "needs_attention": self.needs_attention,
        }


_EVAL_PROMPT = """你是一个AI回答质量评估专家。请从以下维度评估这个回答（1-5分）：

1. **相关性** (relevance): 回答是否针对用户问题
2. **准确性** (accuracy): 信息是否正确、无虚构
3. **有用性** (helpfulness): 是否真正帮助用户解决问题
4. **完整性** (completeness): 是否覆盖问题的各个方面

用户问题:
{question}

AI回答:
{answer}

{context_section}

以 JSON 输出:
{{
  "dimensions": [
    {{"name": "relevance", "score": N, "reason": "..."}},
    {{"name": "accuracy", "score": N, "reason": "..."}},
    {{"name": "helpfulness", "score": N, "reason": "..."}},
    {{"name": "completeness", "score": N, "reason": "..."}}
  ],
  "improvement_suggestion": "具体的改进建议..."
}}
只输出 JSON。"""


_ATTENTION_THRESHOLD = 3.0


class AutoEvaluator:
    """自动质量评测器 — 对差评消息进行 LLM 多维评分。"""

    def __init__(self, llm: BaseChatModel | None = None, threshold: float = _ATTENTION_THRESHOLD):
        self._llm = llm or get_chat_model()
        self._threshold = threshold

    async def evaluate(
        self,
        message_id: str,
        question: str,
        answer: str,
        context: str = "",
    ) -> AutoEvalResult:
        context_section = f"检索上下文:\n{context}" if context else ""
        prompt = _EVAL_PROMPT.format(
            question=question,
            answer=answer,
            context_section=context_section,
        )

        try:
            resp = await self._llm.ainvoke([
                SystemMessage(content="你是一个AI回答质量评估专家。"),
                HumanMessage(content=prompt),
            ])
            data = _parse_json(resp.content)
        except Exception:
            logger.exception("auto_eval_failed", message_id=message_id)
            return AutoEvalResult(message_id=message_id)

        dimensions = [
            EvalDimension(name=d["name"], score=d["score"], reason=d.get("reason", ""))
            for d in data.get("dimensions", [])
        ]

        overall = sum(d.score for d in dimensions) / len(dimensions) if dimensions else 0.0

        result = AutoEvalResult(
            message_id=message_id,
            overall_score=overall,
            dimensions=dimensions,
            improvement_suggestion=data.get("improvement_suggestion", ""),
            needs_attention=overall < self._threshold,
        )

        logger.info(
            "auto_eval_complete",
            message_id=message_id,
            score=round(overall, 2),
            attention=result.needs_attention,
        )
        return result


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("json_parse_failed", text=text[:200])
        return {}
