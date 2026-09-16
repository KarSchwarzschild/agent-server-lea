"""解析结果的 page_count 与 md_content 契约。

page_count 的用途是注入文本里报"共 N 页",以及区分两种看起来一样的情况：

    md 为空 + page_count == 0    文件真的没内容
    md 为空 + page_count > 0     有 N 页但抽不出文本（扫描件/纯图片）

第二种如果报成第一种,模型会说"你的文件好像是空的",而实际上有 20 页内容。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "lea-parser" / "src"))

pptx = pytest.importorskip("pptx", reason="需要 python-pptx")

from pptx import Presentation  # noqa: E402
from pptx.util import Inches  # noqa: E402

from lea_parser.parsers.pptx_parser import parse_pptx  # noqa: E402


def _make_pptx(path: Path, slide_texts: list[str | None]) -> None:
    """造一个 pptx。None 表示这一页不放任何文字（模拟纯图片页）。"""
    prs = Presentation()
    blank = prs.slide_layouts[6]  # 空白版式
    for text in slide_texts:
        slide = prs.slides.add_slide(blank)
        if text is not None:
            box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
            box.text_frame.text = text
    prs.save(str(path))


def test_page_count_counts_all_slides_including_empty(tmp_path):
    """page_count 必须是 slides 的真实条数,不是 text_blocks 的长度。

    纯图片页不进 text_blocks（parser 里 `if slide_texts:`）,所以两者在有图片页时
    不等。报错了用户会以为文件传坏了 —— 12 页的课件显示"共 8 页"。
    """
    f = tmp_path / "mixed.pptx"
    _make_pptx(f, ["第一页", None, "第三页", None, None])

    result = parse_pptx(str(f))
    assert result["page_count"] == 5, "应报 5 页（含 3 个空白页）"
    assert len(result["text_blocks"]) == 2, "只有 2 页有文字"


def test_md_content_has_no_page_markers(tmp_path):
    """md 里**不插**页码标记。

    试过插 `[第 N 页]`,已回退：模型改文件是在沙箱里 prs.slides[i] 直接定位的,那才是
    真相；就算用户说"改讲卷积的那一页",模型也能在沙箱里逐页 dump 文本自己找。
    插标记只省一次往返,代价是污染正文（PDF 那边更糟 —— 会丢掉 MinerU 的排版）。
    """
    f = tmp_path / "plain.pptx"
    _make_pptx(f, ["封面", "卷积神经网络"])

    md = parse_pptx(str(f))["md_content"]
    assert "[第 1 页]" not in md
    assert "[第 " not in md
    assert "封面" in md and "卷积神经网络" in md


def test_text_blocks_keep_page_idx(tmp_path):
    """page_idx 仍然保留在 text_blocks 里 —— 分块入库和检索要用它定位来源页。"""
    f = tmp_path / "idx.pptx"
    _make_pptx(f, ["A", None, "C"])

    blocks = parse_pptx(str(f))["text_blocks"]
    assert [b["page_idx"] for b in blocks] == [0, 2], "第 3 页的 page_idx 应是 2 不是 1"


def test_empty_deck_reports_zero(tmp_path):
    f = tmp_path / "empty.pptx"
    _make_pptx(f, [])
    result = parse_pptx(str(f))
    assert result["page_count"] == 0
    assert result["md_content"] == ""


def test_all_image_deck_distinguishable_from_empty(tmp_path):
    """全是图片页的 PPT：md 空但 page_count > 0。

    这是注入文本里"这 N 页里没有提取到文本 —— 可能是扫描件或纯图片文档"那个分支的
    输入。没有 page_count 就无法跟"空文件"区分。
    """
    f = tmp_path / "images.pptx"
    _make_pptx(f, [None, None, None])

    result = parse_pptx(str(f))
    assert result["md_content"] == ""
    assert result["page_count"] == 3


def test_dispatcher_fills_page_count_for_all_formats(tmp_path):
    """非分页格式返回 0,而且键一定存在 —— 下游不用到处 .get()。"""
    from lea_parser.parsers.dispatcher import parse_document

    txt = tmp_path / "note.txt"
    txt.write_text("纯文本", encoding="utf-8")

    result = parse_document(str(txt))
    assert result["page_count"] == 0, "txt 没有页的概念"
    for key in ("md_content", "text_blocks", "image_items", "page_count"):
        assert key in result, f"dispatcher 应补齐 {key}"


def test_dispatcher_unknown_ext_still_returns_contract(tmp_path):
    """认不出的扩展名走 parse_text 兜底,契约不变。

    上传侧现在不再对未知格式 415（落 blob 让模型在沙箱里自己处理）,所以这条路会被
    走到的机会变多了。
    """
    from lea_parser.parsers.dispatcher import parse_document

    f = tmp_path / "data.weird"
    f.write_text("some content", encoding="utf-8")

    result = parse_document(str(f))
    assert result["page_count"] == 0
    assert isinstance(result["text_blocks"], list)
