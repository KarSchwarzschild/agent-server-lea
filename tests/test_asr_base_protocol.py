"""基座 ASR 协议：base64 裸 PCM，不是 OpenAI 那种 multipart。

## 为什么要这个文件

宿主基座的 /v1/audio/transcriptions 和 OpenAI 的**路径一样但请求体完全不同**：

    OpenAI   multipart 文件上传，files={"file": ...}
    基座     JSON，audio=base64(裸 PCM) + audio_encoding + request_format
             + status + language，且明确要求"音频位深 16，采样率 16000"

原来 _asr_transcribe 只实现了 OpenAI 那种。照原样把基座配进 retrieval_models，
请求会被拒，而 except 把异常吞成空字符串 —— 表现是"上传音频成功但搜不到"，
看不出是协议不对。这类静默失败这个库里已经有好几处来源。

## 顺带钉住的

model 的 JSON 列叫 **config**，不是 extra（RetrievalModel 没有 extra 列）。
取错名字不报错，只会永远拿到空字典 —— 协议配置读不到、静默走默认。
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import wave
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "packages" / "lea-rag" / "src")
)

pytest.importorskip("httpx", reason="需要 httpx")

from lea_rag.services.kb_processor import DocumentProcessor  # noqa: E402

_to_pcm = DocumentProcessor._wav_to_pcm16k

# 16k / 16bit / 单声道 → 每秒 32000 字节
BYTES_PER_SEC = 32000


def mk_wav(rate: int, channels: int, width: int, seconds: float = 1.0) -> str:
    n = int(rate * seconds)
    f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    with wave.open(f.name, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x40" * (n * channels * width))
    return f.name


class FakeModel:
    """替代 RetrievalModel。字段名要和真模型一致 —— 这正是要钉的东西之一。"""

    def __init__(self, config=None, api_key="k", name="funasr"):
        self.api_base = "http://asr.internal"
        self.api_key = api_key
        self.model_name = name
        self.name = name
        self.config = config if config is not None else {}


# ── PCM 归一 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "rate,channels,width,label",
    [
        (16000, 1, 2, "已是目标格式"),
        (44100, 2, 2, "CD 质量立体声"),
        (8000, 1, 1, "8k 8bit"),
        (48000, 2, 4, "48k 立体声 32bit"),
    ],
)
def test_any_wav_is_normalized_to_16k_mono_16bit(rate, channels, width, label):
    """基座明确要求 16bit / 16000Hz。不归一的后果是识别结果乱码或为空 ——
    而那同样不报错。
    """
    p = mk_wav(rate, channels, width, seconds=1.0)
    try:
        pcm = _to_pcm(p)
        # ratecv 的重采样会有几帧误差，给 400 字节（12.5ms）容差
        assert abs(len(pcm) - BYTES_PER_SEC) < 400, f"{label}: {len(pcm)} 字节"
    finally:
        os.unlink(p)


def test_pcm_is_raw_without_wav_header():
    """传的是裸 PCM，不能带 RIFF 头 —— 带了会被当成音频数据解。"""
    p = mk_wav(16000, 1, 2)
    try:
        assert not _to_pcm(p).startswith(b"RIFF")
    finally:
        os.unlink(p)


def test_non_wav_raises_rather_than_returning_empty():
    """镜像里没有 ffmpeg，mp3/m4a/flac/ogg 读不了。必须抛，让上层记日志，
    不能静默当成"这段音频没内容"。
    """
    f = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    f.write(b"ID3\x04\x00\x00\x00\x00\x00\x00fake mp3")
    f.close()
    try:
        with pytest.raises(Exception):
            _to_pcm(f.name)
    finally:
        os.unlink(f.name)


# ── 请求协议 ────────────────────────────────────────────────────────────────


def _capture(monkeypatch) -> dict:
    """拦住 httpx.post，把请求存下来。"""
    seen: dict = {}

    class Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"text": "识别出来的文字"}

    def fake_post(url, **kw):
        seen["url"] = url
        seen.update(kw)
        return Resp()

    import httpx

    monkeypatch.setattr(httpx, "post", fake_post)
    return seen


def test_base_protocol_sends_json_with_base64_pcm(monkeypatch):
    seen = _capture(monkeypatch)
    p = mk_wav(16000, 1, 2)
    try:
        out = DocumentProcessor._asr_transcribe(None, FakeModel(), p)
    finally:
        os.unlink(p)

    assert out == "识别出来的文字"
    assert seen["url"].endswith("/v1/audio/transcriptions")
    body = seen["json"]
    assert body["audio_encoding"] == "base64"
    assert body["request_format"] == "pcm"
    assert body["status"] == 2, "整段一次性提交，status 必须是 2（最后一帧）"
    assert body["language"] == "zh_cn"
    # audio 必须是能解回 PCM 的 base64
    assert len(base64.b64decode(body["audio"])) > 0
    assert "files" not in seen, "基座协议不该走 multipart"


def test_base_protocol_is_the_default(monkeypatch):
    """config 为空时走基座协议 —— 现在接的就是基座。"""
    seen = _capture(monkeypatch)
    p = mk_wav(16000, 1, 2)
    try:
        DocumentProcessor._asr_transcribe(None, FakeModel(config={}), p)
    finally:
        os.unlink(p)
    assert "json" in seen and "files" not in seen


def test_openai_protocol_sends_multipart(monkeypatch):
    """保留 OpenAI 兼容路径，换 ASR 供应商时不用改代码。"""
    seen = _capture(monkeypatch)
    p = mk_wav(16000, 1, 2)
    try:
        DocumentProcessor._asr_transcribe(
            None, FakeModel(config={"protocol": "openai"}), p
        )
    finally:
        os.unlink(p)
    assert "files" in seen and "json" not in seen


def test_language_is_configurable(monkeypatch):
    seen = _capture(monkeypatch)
    p = mk_wav(16000, 1, 2)
    try:
        DocumentProcessor._asr_transcribe(
            None, FakeModel(config={"language": "en_us"}), p
        )
    finally:
        os.unlink(p)
    assert seen["json"]["language"] == "en_us"


def test_config_field_name_is_config_not_extra():
    """钉住字段名。RetrievalModel 的 JSON 列是 config —— 代码里写 extra 会
    永远拿到空字典，协议配置静默失效。
    """
    from lea_rag.db.models import RetrievalModel

    cols = {c.name for c in RetrievalModel.__table__.columns}
    assert "config" in cols
    assert "extra" not in cols, "模型加了 extra 列的话，_asr_transcribe 要跟着改"


def test_failure_raises_and_logs(monkeypatch):
    """base_pcm 失败必须抛 + 打日志。

    这条原来叫 test_failure_returns_empty_and_logs，钉的是"返回空字符串、
    上层据此跳过"。那个前提在加分段转写时被推翻了：分段之后"返回空"会退化成
    "返回残缺拼接"（第 3 段失败、其余成功 → 文本缺中间一块且读不出来缺了什么），
    所以 base_pcm 这条路改成一律抛。

    日志仍然必须打，而且要带 protocol 和 model —— 配错协议和服务真挂了，
    表现都是转写失败，没这两个字段分不开。

    openai 分支保持返回空（它不分段，没有残缺问题），见
    tests/test_asr_segmentation.py::test_openai_protocol_still_swallows_errors。
    """
    import httpx

    def boom(url, **kw):
        raise httpx.ConnectError("连不上")

    monkeypatch.setattr(httpx, "post", boom)
    logged = []
    import lea_rag.services.kb_processor as M

    monkeypatch.setattr(M.logger, "warning", lambda *a, **k: logged.append((a, k)))

    p = mk_wav(16000, 1, 2)
    try:
        with pytest.raises(RuntimeError) as ei:
            DocumentProcessor._asr_transcribe(None, FakeModel(), p)
    finally:
        os.unlink(p)

    assert "1/1" in str(ei.value), "报错要说清是第几段"
    assert logged, "失败没打日志"
    kw = logged[0][1]
    assert kw.get("protocol") == "base_pcm"
    assert kw.get("model") == "funasr"


def test_missing_file_returns_empty():
    assert DocumentProcessor._asr_transcribe(None, FakeModel(), "/nonexistent.wav") == ""


def test_payload_is_json_serializable(monkeypatch):
    """base64 之后必须是纯 str，不能漏 .decode() —— bytes 进 json 会抛。"""
    seen = _capture(monkeypatch)
    p = mk_wav(16000, 1, 2)
    try:
        DocumentProcessor._asr_transcribe(None, FakeModel(), p)
    finally:
        os.unlink(p)
    json.dumps(seen["json"])  # 抛就是失败
