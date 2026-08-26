"""
Harness CLI 入口。

使用方式：
    python -m lea_graph.harness run --dataset data/eval/intent_v1.json --suite intent --output reports/
    python -m lea_graph.harness run --suite rag --dataset data/eval/rag_v1.json
"""

from __future__ import annotations

import argparse
import asyncio

from lea_graph.harness.datasets import EvalDataset
from lea_graph.harness.reports import report_to_markdown, save_report
from lea_graph.harness.runner import EvalRunner


def main():
    parser = argparse.ArgumentParser(description="LEA Platform Evaluation Harness")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run evaluation suite")
    run_parser.add_argument("--dataset", required=True, help="Path to evaluation dataset (JSON)")
    run_parser.add_argument("--suite", default="intent", choices=["intent", "rag", "e2e", "all"])
    run_parser.add_argument("--output", default="reports/", help="Output directory for reports")

    args = parser.parse_args()

    if args.command == "run":
        asyncio.run(_run_eval(args))
    else:
        parser.print_help()


async def _run_eval(args):
    dataset = EvalDataset.from_json(args.dataset)
    print(f"Loaded dataset: {dataset.name} ({len(dataset)} samples)")

    runner = EvalRunner()

    # TODO: 注册实际的 judge（需要 router_fn 等依赖）
    # runner.register_suite("intent", IntentJudge(router_fn))
    # runner.register_suite("rag", RAGJudge())

    if args.suite == "all":
        reports = await runner.run_all(dataset)
        for name, report in reports.items():
            print(report.summary())
            json_path, md_path = save_report(report, args.output)
            print(f"Saved: {json_path}, {md_path}")
    else:
        report = await runner.run(args.suite, dataset)
        print(report.summary())
        json_path, md_path = save_report(report, args.output)
        print(f"Saved: {json_path}, {md_path}")


if __name__ == "__main__":
    main()
