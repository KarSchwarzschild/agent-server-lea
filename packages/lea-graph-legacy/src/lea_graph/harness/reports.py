"""评测报告生成 — Markdown + JSON。"""

from __future__ import annotations

import json
from pathlib import Path

from lea_graph.harness.runner import EvalReport


def report_to_markdown(report: EvalReport) -> str:
    """将评测报告转换为 Markdown 格式。"""
    lines = [
        f"# 评测报告: {report.suite}",
        "",
        f"- **数据集**: {report.dataset_name}",
        f"- **样本数**: {report.samples_evaluated}",
        f"- **耗时**: {report.duration_seconds:.1f}s",
        f"- **时间**: {report.timestamp}",
        "",
    ]

    if "accuracy" in report.metrics:
        lines.append(f"## 整体指标")
        lines.append(f"- Accuracy: **{report.metrics['accuracy']:.2%}**")

    if "macro" in report.metrics:
        m = report.metrics["macro"]
        lines.append(f"- Macro P/R/F1: {m['precision']:.4f} / {m['recall']:.4f} / **{m['f1']:.4f}**")

    if "per_class" in report.metrics:
        lines.extend(["", "## Per-class 指标", "", "| 类别 | Precision | Recall | F1 | Support |", "|------|-----------|--------|-----|---------|"])
        for cls in report.metrics["per_class"]:
            lines.append(f"| {cls['label']} | {cls['precision']:.4f} | {cls['recall']:.4f} | {cls['f1']:.4f} | {cls['support']} |")

    if report.errors:
        lines.extend(["", f"## 错误样本 ({len(report.errors)})", ""])
        for err in report.errors[:20]:
            lines.append(f"- **{err.get('input', '')[:60]}** → 期望 `{err.get('expected')}`, 实际 `{err.get('predicted')}`")

    return "\n".join(lines)


def save_report(report: EvalReport, output_dir: str | Path) -> tuple[Path, Path]:
    """保存评测报告为 JSON + Markdown。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / f"{report.suite}_{report.dataset_name}.json"
    md_path = output_dir / f"{report.suite}_{report.dataset_name}.md"

    json_path.write_text(
        json.dumps(
            {"suite": report.suite, "dataset": report.dataset_name, "metrics": report.metrics,
             "samples": report.samples_evaluated, "duration": report.duration_seconds,
             "errors": report.errors, "timestamp": report.timestamp},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )

    md_path.write_text(report_to_markdown(report), encoding="utf-8")

    return json_path, md_path
