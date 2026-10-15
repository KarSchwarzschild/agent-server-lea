"""lea-parser 指标：序列真的产出，且标签基数有界。

这个文件要钉住的是两件在本仓库都真实发生过的事故：

1) **死埋点**。另外三个服务的 metrics.py 里有 10 个 record_* 只有定义、零调用，
   表现是指标在 /metrics 上只有 HELP/TYPE 两行。引用它的告警语法合法、永远不
   触发，面板永远空白 —— 两者看起来都像"这段时间没流量"。
   所以下面不只断言"函数能调通"，而是断言 **generate_latest() 的输出里有数据行**。

2) **基数爆炸**。endpoint 标签取路由模板而不是 request.url.path。404 的路径是
   外部可控的，回退到真实路径等于让扫描器能把序列数顶爆（内存按序列数涨）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "packages" / "lea-parser" / "src")
)

pytest.importorskip("prometheus_client", reason="需要 prometheus-client")

from lea_parser.metrics import (  # noqa: E402
    _endpoint_label,
    record_parse,
    record_quota_rejection,
)
from prometheus_client import generate_latest  # noqa: E402


def _series_lines(prefix: str) -> list[str]:
    """/metrics 输出里以 prefix 开头的**数据行**（排除 HELP/TYPE 注释）。

    死埋点的指标在这里返回空列表 —— 这正是要抓的。
    """
    text = generate_latest().decode("utf-8")
    return [
        ln for ln in text.splitlines()
        if ln.startswith(prefix) and not ln.startswith("#")
    ]


# ── 埋点真的产出序列 ────────────────────────────────────────────────────────

def test_record_parse_produces_series():
    record_parse("pdf", "success", 1.5)

    total = _series_lines('lea_parser_parse_total{format="pdf",status="success"}')
    assert total, "parse_total 没有产出序列 —— 这就是死埋点的表现"
    assert float(total[0].split()[-1]) >= 1

    # histogram 要有 _count 和 _sum，否则 histogram_quantile 算不出东西
    assert _series_lines('lea_parser_parse_duration_seconds_count{format="pdf"}')
    assert _series_lines('lea_parser_parse_duration_seconds_sum{format="pdf"}')
    assert _series_lines('lea_parser_parse_duration_seconds_bucket{format="pdf"'), \
        "没有 _bucket 序列的话 histogram_quantile 永远返回 0 条"


def test_record_parse_error_separate_from_success():
    """失败要能和成功分开看 —— 否则"解析失败率"这个指标没法算。"""
    record_parse("docx", "success", 1.0)
    record_parse("docx", "error", 0.2)

    ok = _series_lines('lea_parser_parse_total{format="docx",status="success"}')
    bad = _series_lines('lea_parser_parse_total{format="docx",status="error"}')
    assert ok and bad
    assert float(bad[0].split()[-1]) >= 1


def test_record_quota_rejection_both_reasons():
    """两个 reason 对应 parse.py 里两个 QuotaError 分支，各自要能单独计数。"""
    record_quota_rejection("rate_limit")
    record_quota_rejection("file_size")

    for reason in ("rate_limit", "file_size"):
        lines = _series_lines(
            f'lea_parser_quota_rejections_total{{reason="{reason}"}}'
        )
        assert lines, f"{reason} 没产出序列"
        assert float(lines[0].split()[-1]) >= 1


def test_unknown_format_does_not_create_empty_label():
    """空扩展名归到 unknown，不留一个 format="" 的序列。"""
    record_parse("", "error", 0.1)
    assert _series_lines('lea_parser_parse_total{format="unknown",status="error"}')
    assert not _series_lines('lea_parser_parse_total{format="",status="error"}')


# ── 基数保护 ────────────────────────────────────────────────────────────────

class _FakeRoute:
    def __init__(self, path: str) -> None:
        self.path = path


class _FakeRequest:
    def __init__(self, scope: dict) -> None:
        self.scope = scope


def test_endpoint_label_uses_route_template():
    """带主键的路径必须收敛成模板，否则每个 id 一条序列。"""
    req = _FakeRequest({"route": _FakeRoute("/usage/{task_id}")})
    assert _endpoint_label(req) == "/usage/{task_id}"


def test_endpoint_label_unmatched_does_not_leak_path():
    """这条是防远程打爆监控的那一条。

    404 没匹配到路由时 scope 里没有 route。实现**不能**回退到 request.url.path ——
    扫描器打 /wp-login.php、/.env、/api/v1/<随机> 就能让序列数无上限增长。
    所以不管来的是什么路径，都只能得到同一个固定值。
    """
    for scope in ({}, {"route": None}):
        assert _endpoint_label(_FakeRequest(scope)) == "unmatched"


def test_scanner_burst_collapses_to_one_label():
    """模拟扫描器：一串互不相同的未匹配路径只能产出一个标签值。"""
    paths = [f"/random-{i}" for i in range(50)] + [
        "/wp-login.php", "/.env", "/.git/config", "/phpmyadmin",
    ]
    labels = {_endpoint_label(_FakeRequest({"url_path": p})) for p in paths}
    assert labels == {"unmatched"}, f"收敛失败，出现 {len(labels)} 个标签值"
