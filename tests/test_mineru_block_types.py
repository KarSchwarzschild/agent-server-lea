"""MinerU content_list 的块类型：带图的块一律进 image_items，不枚举 type。

## 为什么

_parse_content_list 原来写的是 `elif item_type == "image"`。实测（见
scripts/probe_mineru_block_types.py）MinerU 给饼图的 type 是 **chart**：

    type=text   img_path=无  ← 已处理
    type=chart  img_path=有  ← 落到末尾，静默丢掉

图片在 zip 的 images/ 里躺着，images_dict 也收到了，只是没人去取。表现是
"PDF 解析成功、文字都对、图表检索不到" —— 又一个长得像成功的失败。

MinerU 的 type 枚举没有公开文档（官方 output_files 页面不列，真正的定义在
docvortex 包里，而我们只调云端 API 不装它）。所以枚举 type 是个只会漏的
办法。改成按 img_path 判断。

## 这个文件钉的

  - chart / image / 任意未知 type，只要带 img_path 就进 image_items
  - caption 跟着 type 取对应的键（chart_caption / image_caption / ...）
  - chart 的 content 字段（结构化数据）有值时优先于空 caption
  - zip 里缺图时打警告，不静默跳过
  - 不带 img_path 的块不进 image_items
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "packages" / "lea-rag" / "src")
)

from lea_rag.parsers.parser import _parse_content_list  # noqa: E402

IMG = "images/abc123.jpg"
B64 = {"abc123.jpg": "ZmFrZS1pbWFnZS1ieXRlcw=="}


def test_chart_is_not_dropped():
    """这是线上那个 bug 的直接回归。"""
    out = _parse_content_list(
        [{"type": "chart", "img_path": IMG, "content": "", "page_idx": 0}], B64
    )
    assert len(out["image_items"]) == 1, "chart 被丢掉了"
    assert out["image_items"][0]["base64"] == B64["abc123.jpg"]


def test_image_still_works():
    out = _parse_content_list(
        [{"type": "image", "img_path": IMG, "image_caption": ["图一"], "page_idx": 2}], B64
    )
    assert len(out["image_items"]) == 1
    it = out["image_items"][0]
    assert it["caption"] == "图一"
    assert it["page_idx"] == 2


def test_unknown_type_with_img_path_is_kept():
    """关键一条：没见过的 type 也不能丢。

    枚举 type 的做法每出现一个新 type 就静默丢一类内容。MinerU 没公开枚举，
    所以"没见过"是常态，不是异常。
    """
    for t in ("figure", "diagram", "equation_image", "", "something_new_2027"):
        out = _parse_content_list(
            [{"type": t, "img_path": IMG, "page_idx": 0}], B64
        )
        assert len(out["image_items"]) == 1, f"type={t!r} 被丢掉了"
        assert out["image_items"][0]["block_type"] == t


def test_blocks_without_img_path_do_not_become_images():
    out = _parse_content_list(
        [
            {"type": "text", "text": "正文", "page_idx": 0},
            {"type": "list", "list_items": ["一", "二"], "page_idx": 0},
            {"type": "table", "table_body": "<table><tr><td>格</td></tr></table>",
             "page_idx": 0},
        ],
        B64,
    )
    assert out["image_items"] == []
    assert len(out["text_blocks"]) == 3


def test_caption_key_follows_the_type():
    """chart 用 chart_caption，image 用 image_caption。取错了 caption 就丢。"""
    out = _parse_content_list(
        [{"type": "chart", "img_path": IMG, "chart_caption": ["图 3 销量占比"],
          "page_idx": 0}],
        B64,
    )
    assert out["image_items"][0]["caption"] == "图 3 销量占比"


def test_caption_accepts_a_bare_string():
    """有的块给的是字符串而不是列表。按 [0] 取会拿到第一个字符。"""
    out = _parse_content_list(
        [{"type": "chart", "img_path": IMG, "chart_caption": "饼图", "page_idx": 0}],
        B64,
    )
    assert out["image_items"][0]["caption"] == "饼图"


def test_chart_content_is_used_when_no_caption():
    """chart.content 是图表的结构化数据，比 VLM 描述准确，有值时该用。"""
    out = _parse_content_list(
        [{"type": "chart", "img_path": IMG, "content": "红 39% 蓝 31% 黄 30%",
          "chart_caption": [], "page_idx": 0}],
        B64,
    )
    assert out["image_items"][0]["caption"] == "红 39% 蓝 31% 黄 30%"


def test_explicit_caption_wins_over_content():
    out = _parse_content_list(
        [{"type": "chart", "img_path": IMG, "content": "原始数据",
          "chart_caption": ["图题"], "page_idx": 0}],
        B64,
    )
    assert out["image_items"][0]["caption"] == "图题"


def test_missing_image_in_zip_warns(monkeypatch):
    """zip 里没有对应文件 —— 这是"图表丢了"的另一个入口，不能静默。"""
    import lea_rag.parsers.parser as M

    warned = []
    monkeypatch.setattr(M.logger, "warning", lambda *a, **k: warned.append((a, k)))

    out = _parse_content_list(
        [{"type": "chart", "img_path": "images/not_in_zip.jpg", "page_idx": 0}], B64
    )
    assert out["image_items"] == []
    assert warned, "zip 里缺图却没打警告"
    assert warned[0][1].get("block_type") == "chart"


def test_text_and_chart_mixed():
    """真实样本的形状：三段文字 + 一个 chart。"""
    out = _parse_content_list(
        [
            {"type": "text", "text": "图表测试文档", "page_idx": 0},
            {"type": "text", "text": "正文标识：蝉蜕银壶编目三七", "page_idx": 0},
            {"type": "text", "text": "下面是一张不含任何文字的饼图。", "page_idx": 0},
            {"type": "chart", "img_path": IMG, "content": "",
             "chart_caption": [], "chart_footnote": [], "page_idx": 0},
        ],
        B64,
    )
    assert len(out["text_blocks"]) == 3
    assert len(out["image_items"]) == 1
    assert out["image_items"][0]["block_type"] == "chart"
