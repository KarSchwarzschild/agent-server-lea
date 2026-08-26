"""
EvalRunner — 批量执行评测用例。

统一入口，支持多种评测套件（intent、rag、e2e）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from lea_graph.harness.datasets import EvalDataset

logger = structlog.get_logger()


@dataclass
class EvalReport:
    suite: str
    dataset_name: str
    metrics: dict[str, Any] = field(default_factory=dict)
    samples_evaluated: int = 0
    duration_seconds: float = 0.0
    errors: list[dict[str, Any]] = field(default_factory=list)
    timestamp: str = ""

    def summary(self) -> str:
        lines = [
            f"=== Eval Report: {self.suite} ===",
            f"Dataset: {self.dataset_name} ({self.samples_evaluated} samples)",
            f"Duration: {self.duration_seconds:.1f}s",
        ]
        if "accuracy" in self.metrics:
            lines.append(f"Accuracy: {self.metrics['accuracy']:.2%}")
        if "macro" in self.metrics:
            m = self.metrics["macro"]
            lines.append(f"Macro F1: {m['f1']:.4f} (P={m['precision']:.4f} R={m['recall']:.4f})")
        if self.errors:
            lines.append(f"Errors: {len(self.errors)} samples misclassified")
        return "\n".join(lines)


class EvalRunner:
    """评测运行器。"""

    def __init__(self):
        self._suites: dict[str, Any] = {}

    def register_suite(self, name: str, judge) -> None:
        self._suites[name] = judge

    async def run(self, suite: str, dataset: EvalDataset) -> EvalReport:
        """执行指定套件的评测。"""
        if suite not in self._suites:
            return EvalReport(
                suite=suite,
                dataset_name=dataset.name,
                errors=[{"error": f"Unknown suite: {suite}"}],
            )

        judge = self._suites[suite]
        start = time.time()

        try:
            result = await judge.evaluate(dataset)
            duration = time.time() - start

            report = EvalReport(
                suite=suite,
                dataset_name=dataset.name,
                metrics=result.metrics if hasattr(result, "metrics") else {},
                samples_evaluated=result.total if hasattr(result, "total") else len(dataset),
                duration_seconds=round(duration, 2),
                errors=result.errors if hasattr(result, "errors") else [],
                timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
            )
            logger.info("eval_completed", suite=suite, duration=round(duration, 2))
            return report

        except Exception as e:
            logger.error("eval_failed", suite=suite, error=str(e))
            return EvalReport(
                suite=suite,
                dataset_name=dataset.name,
                errors=[{"error": str(e)}],
                duration_seconds=round(time.time() - start, 2),
            )

    async def run_all(self, dataset: EvalDataset) -> dict[str, EvalReport]:
        """执行所有已注册的评测套件。"""
        results = {}
        for suite_name in self._suites:
            results[suite_name] = await self.run(suite_name, dataset)
        return results
