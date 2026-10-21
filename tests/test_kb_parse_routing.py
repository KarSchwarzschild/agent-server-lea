"""kb_processor._parse 的路由：不认的格式必须抛错，不能按文本兜底。

## 为什么有这个文件

部署完 ffmpeg 之后 mp4 上传成功、状态 ready、138 个 chunk —— 看着是成功，
实际 chunk 正文是：

    '\\x00\\x00\\x00 ftypisom\\x00\\x00\\x02\\x00isomiso2avc1mp41...'
    '- options: cabac=1 ref=3 deblock=1:0:0 analyse=0x3:0x113 me=hex...'

mp4 的容器头和 x264 编码参数。原因是 _parse 里有**第三份**硬编码格式名单
（另两份在两个上传接口里），mp4 不在其中，于是落到 else 分支：

    with open(file_path, encoding="utf-8", errors="ignore") as f:

errors="ignore" 让任意二进制都能"读成功"。于是文档 ready、error_message 空、
搜索能搜到码流参数。

这类失败比报错难认得多：它长得像成功。所以这里钉三件事：

  1. 纯文本走读文本，不进 parse_document
  2. 白名单内的格式交给 parse_document
  3. 都不认的**抛异常**，绝不按文本读
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "packages" / "lea-rag" / "src")
)

from lea_rag.parsers import parser as P  # noqa: E402


class _Proc:
    """只借 _parse 这一个方法，不构造真的 DocumentProcessor（它要 db session）。"""

    def __init__(self):
        from lea_rag.services.kb_processor import DocumentProcessor

        self._parse = DocumentProcessor._parse.__get__(self, _Proc)


@pytest.fixture
def proc():
    return _Proc()


@pytest.fixture
def txt(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("第一段。\n\n第二段。", encoding="utf-8")
    return str(p)


@pytest.fixture
def fake_mp4(tmp_path):
    """真实 mp4 头 —— 就是当初被读成 chunk 的那串字节。"""
    p = tmp_path / "a.mp4"
    p.write_bytes(
        b"\x00\x00\x00 ftypisom\x00\x00\x02\x00isomiso2avc1mp41"
        b"\x00\x00\x00\x08free\x00\x00nmdat" + b"\x11\x22\x33\x44" * 200
    )
    return str(p)


# ── 1. 纯文本 ───────────────────────────────────────────────────────────────


def test_plain_text_is_read_directly(proc, txt, monkeypatch):
    called = []
    monkeypatch.setattr(P, "parse_document", lambda *a, **k: called.append(1) or {})

    out = proc._parse(txt, "txt")

    assert not called, "txt 不该进 parse_document"
    assert "第一段" in out["md_content"]
    assert out["text_chunks"], "应该切出 chunk"


def test_plain_text_exts_cover_the_obvious_ones():
    for e in ("txt", "md", "json", "yaml", "log"):
        assert e in P.PLAIN_TEXT_EXTS, f"{e} 应该算纯文本"
    for e in ("mp4", "mp3", "pdf", "docx", "png", "xlsx"):
        assert e not in P.PLAIN_TEXT_EXTS, f"{e} 不是纯文本，不能按 UTF-8 读"


# ── 2. 白名单内交给 parse_document ──────────────────────────────────────────


def test_whitelisted_formats_go_to_parse_document(proc, fake_mp4, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        P, "parse_document",
        lambda path, parser=None: seen.update(path=path, parser=parser) or {"md_content": ""},
    )
    monkeypatch.setattr(P, "_has_ffmpeg", lambda: True)

    proc._parse(fake_mp4, "mp4")

    assert seen["path"] == fake_mp4
    assert seen["parser"] == "mineru"


@pytest.mark.parametrize("ext", ["pdf", "docx", "png", "mp3", "wav"])
def test_known_binary_formats_never_read_as_text(proc, tmp_path, ext, monkeypatch):
    """这些格式都该进 parse_document，一个都不能走读文本那条。"""
    p = tmp_path / f"a.{ext}"
    p.write_bytes(b"\x00\x01\x02binary\xff\xfe")

    monkeypatch.setattr(P, "_has_ffmpeg", lambda: True)
    monkeypatch.setattr(P, "parse_document", lambda *a, **k: {"md_content": "parsed"})

    out = proc._parse(str(p), ext)
    assert out["md_content"] == "parsed"


# ── 3. 不认的格式必须抛，这是最要紧的一条 ───────────────────────────────────


def test_unknown_binary_raises_instead_of_chunking_garbage(proc, fake_mp4, monkeypatch):
    """ffmpeg 不可用时 mp4 不在白名单 —— 此时必须抛错，不能读成文本。

    这正是线上发生过的那条路：格式不被支持，但 errors="ignore" 让它"读成功"，
    切出 138 个乱码 chunk，文档显示 ready。
    """
    monkeypatch.setattr(P, "_has_ffmpeg", lambda: False)

    with pytest.raises(RuntimeError) as ei:
        proc._parse(fake_mp4, "mp4")

    assert "mp4" in str(ei.value)


def test_the_error_message_lists_what_is_supported(proc, tmp_path, monkeypatch):
    """报错要能自己回答"那我该传什么"，否则用户只能猜。"""
    p = tmp_path / "a.xyz"
    p.write_bytes(b"\x00\x01")
    monkeypatch.setattr(P, "_has_ffmpeg", lambda: True)

    with pytest.raises(RuntimeError) as ei:
        proc._parse(str(p), "xyz")

    msg = str(ei.value)
    assert "pdf" in msg and "txt" in msg, "报错里该列出支持的格式"


def test_no_errors_ignore_text_fallback_remains(proc):
    """源码级断言：_parse 里不能再出现"读任意文件"的兜底。

    用源码而不是行为断言，因为兜底一旦回来，行为测试要恰好命中那个格式才会红；
    结构断言则是只要那行代码在就红。
    """
    import inspect

    from lea_rag.services.kb_processor import DocumentProcessor

    src = inspect.getsource(DocumentProcessor._parse)
    # 允许 errors="ignore" 出现在纯文本分支里（那是安全的），但它必须在
    # PLAIN_TEXT_EXTS 判断之后，且函数结尾不能是无条件的读文本。
    assert "PLAIN_TEXT_EXTS" in src, "_parse 必须按 PLAIN_TEXT_EXTS 分流"
    assert "raise" in src, "_parse 必须对不认的格式抛错"
    # 不能再有写死的格式元组
    assert '"pdf", "docx", "pptx"' not in src, "_parse 里又出现硬编码格式名单"


# ── 4. ASR 缺模型要报错，不能假装 ready ─────────────────────────────────────


def test_missing_asr_model_raises():
    """音视频除了转写没有别的内容来源，拿不到 ASR 就是失败。

    原来是 logger.warning + return：文档 ready / 0 chunk / 无错误，
    而那条 warning 没有任何消费者。
    """
    import inspect

    from lea_rag.services.kb_processor import DocumentProcessor

    src = inspect.getsource(DocumentProcessor._process_audio)
    assert "asr_not_configured" in src

    # warning 之后的**第一条语句**必须是 raise。
    #
    # 先写成"后 6 行里有 raise"，结果注入一个 return 到 raise 前面照样通过 ——
    # 因为 raise 仍在窗口里，只是永远执行不到。判定要钉第一条，不是"存在一条"。
    after = src.split("asr_not_configured", 1)[1]
    stmts = [
        ln.strip() for ln in after.splitlines()
        if ln.strip() and not ln.strip().startswith(("#", '"', ")"))
    ]
    assert stmts, "warning 后面什么都没有？"
    first = stmts[0]
    assert first.startswith("raise"), (
        f"ASR 缺模型时第一条语句必须是 raise，实际是：{first[:120]}"
    )


def test_empty_transcript_raises():
    """ASR 调通但一个字没转出来，同样没有可检索内容。"""
    import inspect

    from lea_rag.services.kb_processor import DocumentProcessor

    src = inspect.getsource(DocumentProcessor._process_audio)
    assert "got_any" in src, "要跟踪是否真的转出了文字"
    assert "没有转写出任何文字" in src
