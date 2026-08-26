"""
意图识别评测 — 评估路由准确率。

将用户输入发送给意图路由器，对比预期技能/意图，
计算 P/R/F1 和混淆矩阵。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import structlog

from lea_graph.harness.datasets import EvalDataset, EvalSample
from lea_graph.harness.metrics import compute_classification_metrics

logger = structlog.get_logger()


@dataclass
class IntentEvalResult:
    metrics: dict[str, Any]
    errors: list[dict[str, Any]]
    total: int
    correct: int


class IntentJudge:
    """意图识别评测器。"""

    def __init__(self, router_fn: Callable[[str], str]):
        """
        Args:
            router_fn: 意图路由函数，输入文本 → 返回技能名/意图名
        """
        self._router = router_fn

    async def evaluate(self, dataset: EvalDataset) -> IntentEvalResult:
        actuals = []
        predictions = []
        errors = []

        for sample in dataset.samples:
            expected = sample.expected.get("expected_skill", sample.expected.get("expected_intent", ""))

            try:
                predicted = self._router(sample.input_text)
            except Exception as e:
                predicted = "error"
                logger.error("intent_eval_error", input=sample.input_text[:50], error=str(e))

            actuals.append(expected)
            predictions.append(predicted)

            if expected != predicted:
                errors.append({
                    "id": sample.id,
                    "input": sample.input_text,
                    "expected": expected,
                    "predicted": predicted,
                    "confidence": sample.expected.get("confidence_level", ""),
                })

        metrics = compute_classification_metrics(actuals, predictions)
        correct = sum(1 for a, p in zip(actuals, predictions) if a == p)

        return IntentEvalResult(
            metrics=metrics,
            errors=errors,
            total=len(actuals),
            correct=correct,
        )
