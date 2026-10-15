"""核对 alerts.yml 引用的指标是否真的存在。

为什么需要这个：告警引用一个不存在的指标名时，规则能正常加载、永远不触发，
而面板上显示"这块已覆盖"。这比没有告警更糟。本仓库已经踩过一次 ——
CeleryTaskQueueLarge 引用的 lea_rag_celery_queue_size 从来没在代码里存在过。

两类指标分开核：
  lea_*      我们自己埋的，去 .py/.go 源码里找定义。
  temporal_* Temporal SDK 自带的，去 GOMODCACHE 里 SDK 的 constants.go 找。

用法：
    python scripts/check_alert_metrics.py        # 不一致时退出码 1（CI 用）
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import yaml

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = pathlib.Path(__file__).resolve().parent.parent
ALERTS = REPO / "infra" / "prometheus" / "alerts.yml"


def live_lines(text: str) -> str:
    """去掉整行注释 —— 注释里举例的指标名不该被当成引用。"""
    return "\n".join(ln for ln in text.splitlines() if not ln.strip().startswith("#"))


def find_in_source(name: str) -> str | None:
    """在 .py/.go 源码里找指标名。

    只认源码：陈旧的 .pyc 和编译出来的 .exe 里会残留早已删掉的指标名，
    拿二进制当证据这个校验就白做了。
    """
    hit = subprocess.run(
        ["git", "grep", "--no-index", "-l", name, "--",
         "packages/*.py", "packages/*.go"],
        capture_output=True, text=True, cwd=REPO,
    ).stdout.strip()
    return hit.splitlines()[0] if hit else None


def temporal_constants() -> set[str]:
    """从 SDK 的 constants.go 里读出全部 temporal_* 指标名。"""
    gomodcache = subprocess.run(
        ["go", "env", "GOMODCACHE"], capture_output=True, text=True,
        cwd=REPO / "packages" / "lea-agent",
    ).stdout.strip()
    if not gomodcache:
        return set()

    # SDK 版本从 go.mod 里读，不写死
    gomod = (REPO / "packages" / "lea-agent" / "go.mod").read_text(encoding="utf-8")
    m = re.search(r"go\.temporal\.io/sdk\s+(v[\w.\-]+)", gomod)
    if not m:
        return set()

    const_file = (
        pathlib.Path(gomodcache) / f"go.temporal.io/sdk@{m.group(1)}"
        / "internal" / "common" / "metrics" / "constants.go"
    )
    if not const_file.is_file():
        print(f"  （找不到 {const_file}，跳过 temporal_* 核对）")
        return set()

    text = const_file.read_text(encoding="utf-8")
    # 形如：XxxCounter = TemporalMetricsPrefix + "activity_execution_failed"
    return {
        "temporal_" + suffix
        for suffix in re.findall(r'TemporalMetricsPrefix \+ "([a-z0-9_]+)"', text)
    }


def main() -> int:
    raw = ALERTS.read_text(encoding="utf-8")
    yaml.safe_load(raw)  # 先确认 YAML 合法
    live = live_lines(raw)

    missing: list[str] = []

    lea_names = sorted(set(re.findall(r"\blea_[a-z0-9_]+\b", live)))
    print(f"lea_* 指标 {len(lea_names)} 个：")
    for name in lea_names:
        where = find_in_source(name)
        if where:
            print(f"  [OK]   {name}")
        else:
            print(f"  [MISS] {name}")
            missing.append(name)

    known = temporal_constants()
    temporal_names = sorted(set(re.findall(r"\btemporal_[a-z0-9_]+\b", live)))
    print(f"\ntemporal_* 指标 {len(temporal_names)} 个"
          f"（SDK 里共 {len(known)} 个可用）：")
    for name in temporal_names:
        # histogram_quantile 要用 _bucket 后缀，那不是 SDK 的指标名本身
        base = name[: -len("_bucket")] if name.endswith("_bucket") else name
        if not known or base in known:
            print(f"  [OK]   {name}")
        else:
            print(f"  [MISS] {name}（SDK 里没有这个名字）")
            missing.append(name)

    print()
    if missing:
        print(f"!! {len(missing)} 个指标不存在，这些告警是哑炮：{missing}")
        return 1
    print("所有告警引用的指标都存在")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
