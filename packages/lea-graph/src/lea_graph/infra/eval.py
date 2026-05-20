"""评测框架接口（骨架实现）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class EvalResult:
    """评测结果。"""

    score: float
    metrics: dict[str, float] = field(default_factory=dict)
    feedback: str | None = None


class EvalProtocol(Protocol):
    """可插拔的评测器接口。"""

    async def evaluate(self, query: str, response: str, context: dict[str, Any]) -> EvalResult: ...


class DefaultEvaluator:
    """默认评测器 — 基于简单规则。后续可替换为 LLM-as-Judge。"""

    async def evaluate(self, query: str, response: str, context: dict[str, Any]) -> EvalResult:
        score = 0.5

        if len(response) > 50:
            score += 0.1
        if len(response) > 200:
            score += 0.1
        if any(kw in response for kw in ["根据", "研究表明", "文献显示"]):
            score += 0.1

        return EvalResult(
            score=min(score, 1.0),
            metrics={"response_length": len(response), "has_evidence": score > 0.6},
        )
