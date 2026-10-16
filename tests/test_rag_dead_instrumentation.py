"""lea-rag 的四个埋点：序列真的产出，状态分得开。

这四个 record_* 此前都是只有定义、零调用点，于是指标在 /metrics 上只有
HELP/TYPE 两行，KBSearchHighLatency 和 EmbeddingHighLatency 两条告警永远不触发。

这个文件钉住的是"接上了"，而且钉住几个**容易悄悄退化**的设计决定：
  - 缓存命中只计数、不进延迟直方图（否则命中率一高 P95 被稀释，告警被掩盖）
  - 部分失败单独记 partial（它照常 200 返回残缺结果，HTTP 5xx 指标抓不到）
  - retry 和 failed 分开（max_retries=2，混记会让失败数虚高三倍）
  - 向量库查询失败也要记（两条 search 路径都吞异常返回 []）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "packages" / "lea-rag" / "src")
)

pytest.importorskip("prometheus_client", reason="需要 prometheus-client")

from prometheus_client import generate_latest  # noqa: E402

from lea_rag.metrics import (  # noqa: E402
    record_document_parse,
    record_embedding,
    record_kb_search,
    record_vector_db_query,
)


def _line(prefix: str) -> str | None:
    for ln in generate_latest().decode("utf-8").splitlines():
        if ln.startswith(prefix) and not ln.startswith("#"):
            return ln
    return None


def _val(prefix: str) -> float:
    ln = _line(prefix)
    return float(ln.split()[-1]) if ln else 0.0


def _hist_count(name: str) -> float:
    return _val(f"{name}_count")


# ── KB 检索 ─────────────────────────────────────────────────────────────────

def test_kb_search_produces_series():
    record_kb_search("success", 0.3)
    assert _line('lea_rag_kb_search_total{status="success"}'), "埋点没接上"
    assert _line("lea_rag_kb_search_duration_seconds_count")


def test_kb_search_has_no_kb_id_label():
    """kb_id 被刻意去掉了 —— 知识库由用户创建，数量无上限，是一条无界序列增长
    路径；而 KBSearchHighLatency 用 sum by (le) 把它聚合掉了，留着只付成本。
    """
    record_kb_search("success", 0.1)
    text = generate_latest().decode("utf-8")
    for ln in text.splitlines():
        if ln.startswith("lea_rag_kb_search") and not ln.startswith("#"):
            assert "kb_id" not in ln, f"kb_id 标签又回来了: {ln}"


def test_cache_hit_counts_but_does_not_enter_histogram():
    """缓存命中是 1ms 级的。进了直方图就会稀释 P95 —— 命中率一高，后端真变慢
    也压不过 1 秒阈值，KBSearchHighLatency 被缓存掩盖。
    """
    before_count = _hist_count("lea_rag_kb_search_duration_seconds")
    before_total = _val('lea_rag_kb_search_total{status="cache_hit"}')

    record_kb_search("cache_hit")  # 不传 duration

    assert _val('lea_rag_kb_search_total{status="cache_hit"}') == before_total + 1, \
        "缓存命中没被计数 —— 那就算不出命中率了"
    assert _hist_count("lea_rag_kb_search_duration_seconds") == before_count, \
        "缓存命中进了延迟直方图，会稀释 P95"


def test_partial_is_distinct_from_success():
    """部分模型组失败时函数照常 200 返回残缺结果，HTTP 5xx 指标抓不到。"""
    before = _val('lea_rag_kb_search_total{status="success"}')
    record_kb_search("partial", 0.5)
    assert _line('lea_rag_kb_search_total{status="partial"}')
    assert _val('lea_rag_kb_search_total{status="success"}') == before, \
        "partial 被算进 success 了"


# ── 向量库 ──────────────────────────────────────────────────────────────────

def test_vector_db_search_paths_are_separate():
    """dense 和 bm25 分开 —— 不然不知道是哪条路慢。"""
    record_vector_db_query("search_dense", 0.02)
    record_vector_db_query("search_bm25", 0.05)
    assert _line('lea_rag_vector_db_queries_total{operation="search_dense"}')
    assert _line('lea_rag_vector_db_queries_total{operation="search_bm25"}')


def test_vector_db_failure_is_recorded():
    """两条 search 路径都吞异常返回 []，向量库挂了的表现是"什么都没搜到"而接口
    照常 200。这个指标是唯一能把"库挂了"和"真的没匹配内容"分开的东西。
    """
    record_vector_db_query("search_dense_error", 0.01)
    assert _line('lea_rag_vector_db_queries_total{operation="search_dense_error"}'), \
        "失败没被记 —— 静默降级就看不见了"


# ── 向量化 ──────────────────────────────────────────────────────────────────

def test_embedding_success_and_error_separate():
    record_embedding("success", 0.4)
    record_embedding("error", 0.1)
    assert _line('lea_rag_embedding_total{status="success"}')
    assert _line('lea_rag_embedding_total{status="error"}')
    assert _line("lea_rag_embedding_duration_seconds_count")


# ── 文档解析 ────────────────────────────────────────────────────────────────

def test_document_parse_retry_distinct_from_failed():
    """max_retries=2，一次彻底失败产生 retry、retry、failed 三笔。

    混记成一个 failed 的话，看到的失败数是真实失败文档数的三倍，而"重试了但
    最后成功了"完全看不见 —— 后者恰恰是上游不稳的早期信号。
    """
    record_document_parse("retry", 1.0)
    record_document_parse("failed", 2.0)
    record_document_parse("success", 3.0)
    for status in ("retry", "failed", "success"):
        assert _line(f'lea_rag_document_parse_total{{status="{status}"}}'), \
            f"{status} 没产出序列"


def test_document_parse_duration_optional():
    """签名允许不传 duration（只计数）。传 None 时不该往直方图里塞 0。"""
    before = _hist_count("lea_rag_document_parse_duration_seconds")
    record_document_parse("success")
    assert _hist_count("lea_rag_document_parse_duration_seconds") == before, \
        "duration=None 时不该记入直方图"
