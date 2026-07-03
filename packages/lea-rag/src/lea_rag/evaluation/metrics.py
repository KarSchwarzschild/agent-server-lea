"""RAG 质量指标 — context precision / recall / faithfulness。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RAGMetrics:
    context_precision: float = 0.0
    context_recall: float = 0.0
    faithfulness: float = 0.0
    hallucination_rate: float = 0.0
    answer_relevance: float = 0.0

    def summary(self) -> dict:
        return {
            "context_precision": round(self.context_precision, 4),
            "context_recall": round(self.context_recall, 4),
            "faithfulness": round(self.faithfulness, 4),
            "hallucination_rate": round(self.hallucination_rate, 4),
            "answer_relevance": round(self.answer_relevance, 4),
        }


def compute_context_precision(
    relevant_indices: list[bool],
) -> float:
    """Context Precision@K — 检索结果中相关文档的加权位置分数。

    relevant_indices: 按检索排名排列，True 表示该位置的文档与问题相关。
    """
    if not relevant_indices or not any(relevant_indices):
        return 0.0

    score = 0.0
    hits = 0
    for i, is_relevant in enumerate(relevant_indices, 1):
        if is_relevant:
            hits += 1
            score += hits / i
    return score / sum(relevant_indices)


def compute_context_recall(
    retrieved_facts: set[str],
    ground_truth_facts: set[str],
) -> float:
    """Context Recall — ground truth 事实被检索上下文覆盖的比例。"""
    if not ground_truth_facts:
        return 1.0
    covered = retrieved_facts & ground_truth_facts
    return len(covered) / len(ground_truth_facts)
