"""图片 chunk 的上下文窗口 + 页码归一 + 章节继承。

## 为什么要带上下文

图片单靠 VLM 描述检索不出来。一张柱状图的描述是"柱状图显示三个季度的数据对比"，
用户问"第三季度营收多少"匹配不上 —— 描述里没有"营收"这个词，它在图片周围的
正文里。这是 RAGFlow 的 Image context window 解决的问题（它那边按 token 数配）。

文本侧早就有同一个机制（parser.py 的 window_content：前后各一个 chunk 一起 embed，
content 仍存窄的那份），图片侧一直没有。

## 顺带修的两个既存 bug

1. **页码差 1**：解析器给的 page_idx 是 0-based，而 chunk_text_blocks 把文本转成
   page_idx + 1。于是同一物理页上的图和文页码差 1 —— 按页找上下文会错位一页，
   而且 source_url 的 `#page=` 指向前一页（PDF 阅读器页码是 1-based，点引用跳错）。

2. **section_id 写死 0**：section_boost 靠这一列加成，图片永远拿不到，而同页正文
   拿得到。现在改成按页继承。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "packages" / "lea-rag" / "src")
)

pytest.importorskip("sqlalchemy", reason="需要 sqlalchemy")

from lea_rag.services.kb_processor import DocumentProcessor  # noqa: E402

_page = DocumentProcessor._image_page
_compose = DocumentProcessor._compose_image_content


def _ctx(chunks, page, limit):
    """_image_context 是实例方法但不碰 self，直接借用未绑定版本。"""
    return DocumentProcessor._image_context(None, chunks, page, limit)


def mk(content: str, page: int) -> dict:
    return {"content": content, "metadata": {"page": page}}


# ── 页码归一 ────────────────────────────────────────────────────────────────


def test_page_idx_is_normalized_to_one_based():
    """page_idx 是 0-based，要 +1 才和文本 chunk 对齐。"""
    assert _page({"page_idx": 0}) == 1
    assert _page({"page_idx": 4}) == 5


def test_page_key_is_taken_as_is():
    """有的解析器直接给 page，那已经是 1-based。"""
    assert _page({"page": 3}) == 3


def test_missing_page_defaults_to_one():
    assert _page({}) == 1


def test_page_idx_wins_over_page():
    """两个键都在时按 page_idx 算 —— mineru 给的是它。"""
    assert _page({"page_idx": 2, "page": 99}) == 3


# ── 上下文窗口 ──────────────────────────────────────────────────────────────


def test_same_page_text_is_used_as_context():
    chunks = [mk("第三季度营收同比增长 12%", 5), mk("无关的另一页", 1)]
    ctx = _ctx(chunks, 5, 500)
    assert "第三季度营收" in ctx
    assert "无关的另一页" not in ctx


def test_adjacent_page_is_borrowed():
    """图片常在页首页尾，相关正文在邻页 —— 只取同页会拿到空上下文。"""
    chunks = [mk("上一页末尾的图题说明", 4)]
    assert "上一页末尾" in _ctx(chunks, 5, 500)


def test_distant_pages_are_not_borrowed():
    """只借相邻一页。再远的内容和这张图没关系，拼进来是噪声。"""
    chunks = [mk("隔了三页的内容", 2)]
    assert _ctx(chunks, 5, 500) == ""


def test_nearest_page_comes_first():
    """同页优先于邻页 —— 同页正文和图片的相关性更高。"""
    chunks = [mk("邻页内容", 4), mk("同页内容", 5)]
    ctx = _ctx(chunks, 5, 500)
    assert ctx.index("同页内容") < ctx.index("邻页内容")


def test_context_is_truncated_to_limit():
    """整页正文可能几千字，而描述只有几十字 —— 全拼进去等于让正文淹掉描述，
    这个 chunk 就变成"又一份正文"，失去了"这是一张图"的区分度。
    """
    chunks = [mk("甲" * 5000, 5)]
    assert len(_ctx(chunks, 5, 100)) == 100


def test_limit_zero_disables_context():
    """0 表示退回原行为（只用描述），要能彻底关掉。"""
    chunks = [mk("同页正文", 5)]
    assert _ctx(chunks, 5, 0) == ""


def test_no_chunks_gives_empty_context():
    assert _ctx([], 5, 500) == ""


def test_blank_chunks_are_skipped():
    chunks = [mk("   ", 5), mk("真正的内容", 5)]
    assert _ctx(chunks, 5, 500).strip() == "真正的内容"


# ── 内容拼接 ────────────────────────────────────────────────────────────────


def test_composed_content_marks_it_as_an_image():
    """命中之后调用方和模型要能一眼看出这是图片，否则模型会把 VLM 的描述
    当成文档原文引用。"""
    out = _compose("柱状图显示三季度对比", 5, "第三季度营收增长 12%")
    assert out.startswith("[图片 p5]")
    assert "柱状图显示三季度对比" in out
    assert "第三季度营收增长 12%" in out


def test_composed_content_without_context_is_just_the_caption():
    out = _compose("一张图的描述", 3, "")
    assert out == "一张图的描述"


def test_composed_content_falls_back_when_caption_empty():
    """VLM 没配或返回空时仍要有可读内容，不能是空字符串 —— 空 content
    进了 Milvus 之后 BM25 那一路算不出任何稀疏向量。"""
    assert _compose("", 7, "") == "[图片 p7]"
    assert "[图片 p7]" in _compose("", 7, "周边正文")


# ── 这是这组改动要解决的真实场景 ────────────────────────────────────────────


def test_query_term_only_in_surrounding_text_is_now_covered():
    """判据：用户的查询词不在 VLM 描述里，只在周边正文里。

    改之前图片 chunk 的文本只有描述，这个查询永远匹配不到那张图。
    改之后描述和正文一起进 embedding，才有被召回的可能。

    这条测试钉的是"正文确实进了待 embedding 的文本"，不是检索效果本身 ——
    效果要在真 Milvus 上验（scripts/verify_kb_multimodal_deployed.py）。
    """
    caption = "柱状图显示三个季度的数据对比"       # 没有"营收"
    body = "下图为第三季度营收情况，同比增长 12%"   # 有"营收"
    chunks = [mk(body, 5)]

    enriched = _compose(caption, 5, _ctx(chunks, 5, 500))
    assert "营收" in enriched, "查询词仍然不在待 embedding 的文本里，这组改动没生效"
    assert caption in enriched, "描述被上下文挤掉了"
