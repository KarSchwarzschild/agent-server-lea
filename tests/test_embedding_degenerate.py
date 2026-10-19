"""上游假成功检测：200 + 废向量，必须被认出来。

要防的事：中转 API（tenantapi-ai.ouchn）不可用或 key 过期时**不一定回非 2xx**。
回 200 带一组全零向量的话：

    resp.raise_for_status()   不抛
    _parse_response()         不抛
    record_embedding("success") 照记
    检索拿零向量去算余弦，返回一堆垃圾，HTTP 照常 200

于是没有任何指标会动。这比"搜不到"更糟 —— 搜不到是沉默，这个是自信地给错答案。

这个文件钉住两件事：
  1. _degenerate_reason 能认出三种废向量（条数不符 / 全零 / 空）
  2. 它**不**把正常向量误判成废的（否则就是个只会喊狼来了的检测器）

以及一个刻意的设计决定：**不查维度**。embedding_dim 是每个知识库自己的
（db/models.py 默认 1024），而 EmbeddingSettings.dimension 是 1536，不同模型
本来就不一样，拿全局常量判维度只会造假警报。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "packages" / "lea-rag" / "src")
)

pytest.importorskip("httpx", reason="需要 httpx")

from lea_rag.embeddings.embedding_client import EmbeddingClient  # noqa: E402

_reason = EmbeddingClient._degenerate_reason


# ── 正常向量不能被误判 ──────────────────────────────────────────────────────


def test_normal_vectors_pass():
    assert _reason(["a", "b"], [[0.1, 0.2], [0.3, 0.4]]) is None


def test_single_normal_vector_passes():
    assert _reason(["only"], [[0.5, -0.5, 0.1]]) is None


def test_negative_values_are_fine():
    """全负数是合法向量 —— any() 判的是非零，不是为真。

    这条是防止把检查写成 `if not all(v)` 或 `if not any(x > 0 ...)`：
    -0.3 是有效的 embedding 分量，被判成废的话正常请求会刷 degenerate。
    """
    assert _reason(["x"], [[-0.3, -0.7, -0.1]]) is None


def test_one_zero_component_is_fine():
    """单个分量为 0 正常，只有**整条全零**才是废的。"""
    assert _reason(["x"], [[0.0, 0.0, 0.9, 0.0]]) is None


def test_empty_input_empty_output_is_fine():
    """没发东西自然没向量，不算异常（embed_texts 对空列表直接返回 []）。"""
    assert _reason([], []) is None


# ── 三种废向量必须被认出 ────────────────────────────────────────────────────


def test_all_zero_vector_detected():
    r = _reason(["x"], [[0.0, 0.0, 0.0]])
    assert r is not None and "all_zero_vector" in r


def test_all_zero_in_batch_detected():
    """一批里只有一条全零也要抓 —— 它对应的那个 chunk 会被永久错检索。"""
    r = _reason(["a", "b", "c"], [[0.1], [0.0], [0.3]])
    assert r is not None and "all_zero_vector" in r and "index=1" in r


def test_count_mismatch_detected():
    """发 3 条回 2 条：后面 zip 会错位，把 A 的文本配上 B 的向量。"""
    r = _reason(["a", "b", "c"], [[0.1], [0.2]])
    assert r is not None and "count_mismatch" in r
    assert "sent=3" in r and "got=2" in r


def test_empty_response_for_nonempty_input_detected():
    r = _reason(["a"], [])
    assert r is not None and "empty_response" in r


def test_empty_vector_inside_batch_detected():
    r = _reason(["a", "b"], [[0.1], []])
    assert r is not None and "empty_vector" in r


# ── 维度不查，是刻意的 ──────────────────────────────────────────────────────


def test_dimension_mismatch_is_not_flagged():
    """1024 和 1536 混在一批里也不报 —— 维度由 Milvus 自己把关。

    这条钉住的是决定本身：如果有人后来加了维度校验，这个测试会红，
    提醒他 embedding_dim 是 per-KB 的，全局常量判不了。
    """
    assert _reason(["a", "b"], [[0.1] * 1024, [0.2] * 1536]) is None


# ── 指标状态互斥 ────────────────────────────────────────────────────────────


def test_degenerate_and_success_are_mutually_exclusive():
    """一次调用只记一笔，否则分母虚高、比率式告警被稀释。

    search_kb 的 partial/success 就踩过这个坑（先记 partial 又 fall through
    记 success）。这里用源码核对：两个 post 方法里各只有一处 record_embedding
    在成功路径上，且用三元表达式选 status。
    """
    import inspect

    src = inspect.getsource(EmbeddingClient)
    # 成功路径上的 record_embedding 调用：三元选 status，不是两条独立语句
    assert src.count('record_embedding("degenerate" if reason else "success"') == 2
    # 失败路径仍然是 error，各一处
    assert src.count('record_embedding("error"') == 2
