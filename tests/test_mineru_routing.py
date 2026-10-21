"""MinerU 路由：本地/云端两种部署下，哪些格式交给它、失败怎么兜底。

## 为什么要分两种模式

MinerU 官方文档（opendatalab.github.io/MinerU）写明：PDF 和图片在四个档位
（Flash/Basic/Standard/Advanced）都可用，而 **Office、OpenDocument、EPUB、
OFD、HTML/MHTML、CSV/TSV 只用本地 Flash 原生解析**。

所以云端模式下把 docx 交给 MinerU 不是"慢一点"，而是拿不到内容。而拿不到内容
的表现是**解析成功但文档是空的** —— 文档状态变 ready，用户看不出有问题，
直到搜不到东西。这类静默失败这个代码库里已经有好几处来源，不该再加一个。

## 这个文件钉的

  - 云端模式只有 pdf 进 MinerU，docx 走 python-docx
  - 本地模式 17 种格式都进 MinerU
  - MinerU 失败一律降级，不让服务抖动变成文档丢失
  - 只有 MinerU 能处理的格式（xlsx 等）失败时**抛异常而不是返回空**
  - 上传白名单从解析能力推导，不写死
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "packages" / "lea-rag" / "src")
)

pytest.importorskip("httpx", reason="需要 httpx")

from lea_rag.parsers import parser as P  # noqa: E402

LOCAL = "http://mineru.internal:8002"


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.delenv("MINERU_URL", raising=False)


@pytest.fixture
def local(monkeypatch):
    monkeypatch.setenv("MINERU_URL", LOCAL)


# ── 格式集合 ────────────────────────────────────────────────────────────────


def test_cloud_mode_only_sends_pdf_to_mineru(cloud):
    """云端只支持 pdf 和图片。图片不走 MinerU（见 _MINERU_NATIVE_EXTS 注释），
    所以这里只剩 pdf。"""
    assert P.mineru_native_exts() == frozenset({"pdf"})


def test_local_mode_sends_office_formats_to_mineru(local):
    exts = P.mineru_native_exts()
    for e in ("docx", "pptx", "xlsx", "epub", "html", "csv", "rtf", "odt", "ofd"):
        assert e in exts, f"{e} 该交给本地 MinerU"


def test_images_never_go_to_mineru(local):
    """MinerU 对图片做 OCR，而知识库要的是整图交给 VLM / multimodal。
    一张示意图 OCR 出来往往是空的。"""
    for e in ("png", "jpg", "jpeg", "webp", "gif", "bmp"):
        assert e not in P.mineru_native_exts()


def test_audio_never_goes_to_mineru(local):
    """MinerU 不做 ASR。"""
    for e in ("mp3", "wav", "m4a", "flac", "ogg"):
        assert e not in P.mineru_native_exts()


# ── 上传白名单 ──────────────────────────────────────────────────────────────


def test_whitelist_grows_when_local_mineru_configured(monkeypatch):
    """配上本地 MinerU 之后白名单自动变宽 —— 这是"从能力推导"的全部意义。"""
    monkeypatch.delenv("MINERU_URL", raising=False)
    before = P.supported_upload_exts()
    monkeypatch.setenv("MINERU_URL", LOCAL)
    after = P.supported_upload_exts()
    assert after > before, "本地 MinerU 没让白名单变宽"
    assert "xlsx" in after and "xlsx" not in before


def test_whitelist_always_has_the_basics(cloud):
    """即使 MinerU 完全不可用，这些格式也该能上传（有本地解析器）。"""
    exts = P.supported_upload_exts()
    for e in ("txt", "md", "pdf", "docx", "pptx", "png", "mp3"):
        assert e in exts


def test_video_is_not_in_whitelist(local):
    """视频在这个平台还没有解析路径 —— lea-rag 的 parse_document 没有 video
    分支（lea-parser 有，但知识库入库走的不是它）。白名单不该收。"""
    exts = P.supported_upload_exts()
    for e in ("mp4", "avi", "mov", "mkv"):
        assert e not in exts


# ── 路由与兜底 ──────────────────────────────────────────────────────────────


def test_docx_uses_local_parser_in_cloud_mode(cloud, monkeypatch):
    calls = []
    monkeypatch.setattr(P, "parse_docx", lambda p: calls.append("docx") or {"ok": 1})
    monkeypatch.setattr(
        P, "parse_pdf_mineru",
        lambda p: calls.append("mineru") or {"ok": 0},
    )
    P.parse_document("/tmp/a.docx")
    assert calls == ["docx"], "云端模式不该把 docx 交给 MinerU"


def test_docx_prefers_mineru_in_local_mode(local, monkeypatch):
    calls = []
    monkeypatch.setattr(P, "parse_docx", lambda p: calls.append("docx") or {"ok": 0})
    monkeypatch.setattr(
        P, "parse_pdf_mineru",
        lambda p: calls.append("mineru") or {"ok": 1},
    )
    P.parse_document("/tmp/a.docx")
    assert calls == ["mineru"], "本地模式该优先用 MinerU"


def test_docx_falls_back_when_mineru_fails(local, monkeypatch):
    """服务抖动不该变成文档丢失。"""
    calls = []

    def boom(p):
        calls.append("mineru")
        raise RuntimeError("连不上")

    monkeypatch.setattr(P, "parse_pdf_mineru", boom)
    monkeypatch.setattr(P, "parse_docx", lambda p: calls.append("docx") or {"ok": 1})
    out = P.parse_document("/tmp/a.docx")
    assert calls == ["mineru", "docx"]
    assert out == {"ok": 1}


def test_pdf_falls_back_to_pymupdf(local, monkeypatch):
    calls = []

    def boom(p):
        raise RuntimeError("挂了")

    monkeypatch.setattr(P, "parse_pdf_mineru", boom)
    monkeypatch.setattr(
        P, "parse_pdf_pymupdf", lambda p: calls.append("pymupdf") or {"ok": 1}
    )
    P.parse_document("/tmp/a.pdf")
    assert calls == ["pymupdf"]


def test_mineru_only_format_raises_instead_of_returning_empty(local, monkeypatch):
    """xlsx 没有本地兜底。MinerU 失败时必须抛 —— 返回空内容会被当成"解析成功
    但文档没内容"，状态变 ready，用户看不出有问题。

    这条是这组改动里最该守住的不变量：**宁可报错，不要静默的空**。
    """
    monkeypatch.setattr(
        P, "parse_pdf_mineru", lambda p: (_ for _ in ()).throw(RuntimeError("挂了"))
    )
    with pytest.raises(RuntimeError, match="只能由本地 MinerU 解析"):
        P.parse_document("/tmp/a.xlsx")


def test_mineru_only_format_in_cloud_mode_also_raises(cloud, monkeypatch):
    """云端模式下 xlsx 压根不会交给 MinerU，但也没有兜底 —— 同样要抛。

    注：正常不会走到这里，上传白名单在 API 层就把 xlsx 拦掉了（见
    test_whitelist_grows_when_local_mineru_configured）。这是第二道防线。
    """
    with pytest.raises(RuntimeError):
        P.parse_document("/tmp/a.xlsx")


def test_image_and_audio_bypass_mineru_entirely(local, monkeypatch):
    calls = []
    monkeypatch.setattr(P, "parse_image", lambda p: calls.append("img") or {})
    monkeypatch.setattr(P, "parse_audio", lambda p: calls.append("audio") or {})
    monkeypatch.setattr(
        P, "parse_pdf_mineru", lambda p: calls.append("mineru") or {}
    )
    P.parse_document("/tmp/a.png")
    P.parse_document("/tmp/a.mp3")
    assert calls == ["img", "audio"], "图片/音频不该经过 MinerU"


# ── mime 映射 ───────────────────────────────────────────────────────────────


def test_each_native_ext_has_a_mime():
    """缺 mime 会退到 application/octet-stream。服务端按 mime 分派的话会失配，
    而失配的表现是"解析出空内容"，不报错 —— 所以这张表必须全。
    """
    missing = [e for e in P._MINERU_NATIVE_EXTS if e not in P._EXT_MIME]
    assert not missing, f"这些格式没有 mime 映射: {missing}"
