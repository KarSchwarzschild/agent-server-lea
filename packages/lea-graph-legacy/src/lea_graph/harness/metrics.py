"""
评测指标计算 — Precision/Recall/F1、混淆矩阵。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ClassMetrics:
    """单个类别的 P/R/F1。"""
    label: str
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    support: int = 0


@dataclass
class ConfusionMatrix:
    """混淆矩阵。"""
    labels: list[str] = field(default_factory=list)
    matrix: list[list[int]] = field(default_factory=list)

    def add(self, actual: str, predicted: str) -> None:
        if actual not in self.labels:
            self.labels.append(actual)
            for row in self.matrix:
                row.append(0)
            self.matrix.append([0] * len(self.labels))
        if predicted not in self.labels:
            self.labels.append(predicted)
            for row in self.matrix:
                row.append(0)
            self.matrix.append([0] * len(self.labels))

        i = self.labels.index(actual)
        j = self.labels.index(predicted)
        self.matrix[i][j] += 1

    def to_dict(self) -> dict[str, Any]:
        return {"labels": self.labels, "matrix": self.matrix}


def compute_classification_metrics(
    actuals: list[str],
    predictions: list[str],
) -> dict[str, Any]:
    """计算分类指标：per-class P/R/F1 + macro/micro 平均。"""
    cm = ConfusionMatrix()
    for a, p in zip(actuals, predictions):
        cm.add(a, p)

    tp_per_class: dict[str, int] = defaultdict(int)
    fp_per_class: dict[str, int] = defaultdict(int)
    fn_per_class: dict[str, int] = defaultdict(int)
    support: dict[str, int] = Counter(actuals)

    for i, label in enumerate(cm.labels):
        tp_per_class[label] = cm.matrix[i][i]
        fp_per_class[label] = sum(cm.matrix[j][i] for j in range(len(cm.labels))) - cm.matrix[i][i]
        fn_per_class[label] = sum(cm.matrix[i]) - cm.matrix[i][i]

    per_class = []
    for label in cm.labels:
        tp = tp_per_class[label]
        fp = fp_per_class[label]
        fn = fn_per_class[label]
        p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
        per_class.append(ClassMetrics(label=label, precision=p, recall=r, f1=f1, support=support.get(label, 0)))

    total_tp = sum(tp_per_class.values())
    total_fp = sum(fp_per_class.values())
    total_fn = sum(fn_per_class.values())

    micro_p = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
    micro_r = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0
    micro_f1 = 2 * micro_p * micro_r / (micro_p + micro_r) if (micro_p + micro_r) > 0 else 0.0

    macro_p = sum(m.precision for m in per_class) / len(per_class) if per_class else 0.0
    macro_r = sum(m.recall for m in per_class) / len(per_class) if per_class else 0.0
    macro_f1 = sum(m.f1 for m in per_class) / len(per_class) if per_class else 0.0

    accuracy = sum(1 for a, p in zip(actuals, predictions) if a == p) / len(actuals) if actuals else 0.0

    return {
        "accuracy": round(accuracy, 4),
        "micro": {"precision": round(micro_p, 4), "recall": round(micro_r, 4), "f1": round(micro_f1, 4)},
        "macro": {"precision": round(macro_p, 4), "recall": round(macro_r, 4), "f1": round(macro_f1, 4)},
        "per_class": [
            {"label": m.label, "precision": round(m.precision, 4), "recall": round(m.recall, 4),
             "f1": round(m.f1, 4), "support": m.support}
            for m in per_class
        ],
        "confusion_matrix": cm.to_dict(),
        "total_samples": len(actuals),
    }
