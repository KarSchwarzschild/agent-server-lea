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

import os
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


# Histogram / Summary 的派生序列后缀。Prometheus 按基名自动生成这三个，源码里
# 只有基名 —— 不剥掉就会把 lea_xxx_duration_seconds_bucket 误报成"不存在"。
DERIVED_SUFFIXES = ("_bucket", "_sum", "_count")


def strip_derived(name: str) -> str:
    for suffix in DERIVED_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def find_in_source(name: str) -> str | None:
    """在 .py/.go 源码里找指标名。

    只认源码：陈旧的 .pyc 和编译出来的 .exe 里会残留早已删掉的指标名，
    拿二进制当证据这个校验就白做了。
    """
    hit = subprocess.run(
        ["git", "grep", "--no-index", "-l", strip_derived(name), "--",
         "packages/*.py", "packages/*.go"],
        capture_output=True, text=True, cwd=REPO,
    ).stdout.strip()
    return hit.splitlines()[0] if hit else None


METRICS_MODULES = (
    "packages/lea-config/src/lea_config/metrics.py",
    "packages/lea-rag/src/lea_rag/metrics.py",
    "packages/lea-tools/src/lea_tools/metrics.py",
)


def dead_instrumentation() -> dict[str, list[str]]:
    """找出"定义了但没人调"的 record_* 埋点函数，以及它们喂的指标名。

    为什么这个检查必须有：指标名存在 ≠ 指标有数。record_xxx() 这种 helper 写在
    metrics.py 里、业务代码里一次都不调的话，指标在 /metrics 上的表现是
      带标签的   —— 一条序列都没有（PromQL 返 0 条）
      无标签的   —— 声明即产出序列，但 count/sum 恒为 0（rate() 返 nan）
    两种都意味着引用它的告警永远不会触发，而上面那个"名字是否存在"的检查会
    一路放过去 —— 名字确实在 metrics.py 里，就在那个没人调的函数旁边。

    2026-10-15 实测：三个服务的 10 个 record_* **全部**没有调用点，21 条告警里
    有 6 条因此是哑炮。
    """
    dead: dict[str, list[str]] = {}
    for rel in METRICS_MODULES:
        path = REPO / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for fn in re.findall(r"^def (record_[a-z0-9_]+)\(", text, re.M):
            hits = subprocess.run(
                ["git", "grep", "--no-index", "-l", fn, "--", "packages/*.py"],
                capture_output=True, text=True, cwd=REPO,
            ).stdout.strip().splitlines()
            callers = [
                h for h in hits
                if not h.replace("\\", "/").endswith("metrics.py")
                and "legacy" not in h
                and ".venv" not in h
            ]
            if callers:
                continue
            # 从函数体里挖出它操作的指标对象名，再映射到指标字符串
            body = re.search(rf"^def {fn}\(.*?(?=^def |\Z)", text, re.M | re.S)
            objs = re.findall(r"^\s+([a-z_][a-z0-9_]*)\.(?:labels|observe|inc|set)",
                              body.group(0), re.M) if body else []
            names = []
            for obj in set(objs):
                m = re.search(rf"^{obj}\s*=\s*\w+\(\s*\n?\s*'([a-z0-9_]+)'", text, re.M)
                if m:
                    names.append(m.group(1))
            dead[f"{rel}::{fn}"] = sorted(set(names))
    return dead


def bad_histogram_quantiles(raw: str) -> list[str]:
    """找出喂了非 _bucket 序列的 histogram_quantile。

    为什么单独查这个：histogram_quantile 要的是带 le 标签的 bucket 序列。直接喂
    基名（lea_x_duration_seconds）语法完全合法、规则正常加载、**永远返回 0 条**，
    于是又是一个哑炮。而上面那个"指标名是否存在"的检查抓不到它 —— 基名在源码里
    确实存在（Histogram 就是用基名定义的），所以会一路 [OK] 放过去。

    2026-10-15 实地踩到：SlowHTTPResponse / DBQueryLatencyP95High /
    KBSearchHighLatency / EmbeddingHighLatency 四条都是这个毛病，在生产跑了
    两天，Prometheus 里查出来是 0 条结果。
    """
    bad = []
    # histogram_quantile(0.95, ...) 里第一个出现的指标名（跳过 rate/sum 这些函数名）
    for call in re.findall(r"histogram_quantile\s*\(\s*[\d.]+\s*,(.{0,400}?)\)\s*[<>=]", raw, re.S):
        names = re.findall(r"\b((?:lea|temporal)_[a-z0-9_]+)\b", call)
        if names and not names[0].endswith("_bucket"):
            bad.append(names[0])
    return bad


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
        base = strip_derived(name)
        if not known or base in known:
            print(f"  [OK]   {name}")
        else:
            print(f"  [MISS] {name}（SDK 里没有这个名字）")
            missing.append(name)

    bad_hq = bad_histogram_quantiles(live)
    print("\nhistogram_quantile 参数检查："
          + (f"{len(bad_hq)} 处有问题" if bad_hq else "全部都喂了 _bucket"))
    for name in bad_hq:
        print(f"  [BAD]  histogram_quantile 喂了 {name}，应该是 {name}_bucket")

    # ── 死埋点 ───────────────────────────────────────────────────────────────
    # 默认只报告、不拦。接通 10 个埋点是一整块活，不该让这个检查把流水线卡在
    # 一个存量问题上。要翻成阻塞：设 LEA_STRICT_INSTRUMENTATION=1。
    dead = dead_instrumentation()
    dead_metrics = {m for names in dead.values() for m in names}
    affected = sorted(
        n for n in lea_names if strip_derived(n) in dead_metrics
    )
    print(f"\n死埋点检查：{len(dead)} 个 record_* 函数没有调用点")
    for where, names in sorted(dead.items()):
        print(f"  [DEAD] {where}")
        for m in names:
            flag = "  <-- 有告警引用它" if m in dead_metrics and any(
                strip_derived(n) == m for n in lea_names
            ) else ""
            print(f"           喂 {m}{flag}")
    if affected:
        print(f"\n  !! 这些指标被告警引用但永远没数（哑炮）：{affected}")

    print()
    if missing:
        print(f"!! {len(missing)} 个指标不存在，这些告警是哑炮：{missing}")
    if bad_hq:
        print(f"!! {len(bad_hq)} 处 histogram_quantile 没用 _bucket，这些告警永远返回 0 条")

    strict = os.environ.get("LEA_STRICT_INSTRUMENTATION") == "1"
    if affected and strict:
        print(f"!! LEA_STRICT_INSTRUMENTATION=1：{len(affected)} 个指标无埋点，判失败")

    if missing or bad_hq or (affected and strict):
        return 1
    if affected:
        print(f"（{len(affected)} 个指标缺埋点，已报告未拦 —— "
              f"设 LEA_STRICT_INSTRUMENTATION=1 可转为阻塞）")
    print("告警引用的指标都存在，histogram_quantile 参数也对")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
