"""视频/音频经 ffmpeg 抽音轨 → 基座 ASR。

## 为什么用 ffmpeg 而不是跨服务调字幕服务

视频入知识库有两条路：

  A  调 subtitle_api（已有服务，FunASR + GPU worker 调度）
     但它收 VideoUrl 不收文件、是异步的、要 chatLoggerId —— 三件事都得先定

  B  装 ffmpeg 自己抽音轨，复用已接好的基座 /v1/audio/transcriptions
     视频和音频走同一条链路，不跨服务

选了 B，因为一个改动解决三个问题：
  1. 视频能进知识库
  2. 音频从「只支持 WAV」放开到 mp3/m4a/flac/ogg（标准库的 wave 只认 WAV）
  3. audioop 在 Python 3.13 被移除（PEP 594），ffmpeg 是那时候的退路

## 这个文件钉的

  - 音视频是**同一条路**：ffmpeg 对 mp4 和 mp3 是同一个命令（-vn 丢视频流）
  - 输出是裸 PCM（16k/16bit/单声道），不带 RIFF 头
  - 无音轨的视频**报错而不是返回空** —— 空会被当成「这段没内容」
  - 白名单随 ffmpeg 在不在动态变化，不写死
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "packages" / "lea-rag" / "src")
)

from lea_rag.parsers import parser as P  # noqa: E402
from lea_rag.services.kb_processor import DocumentProcessor as D  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg")

BYTES_PER_SEC = 32000  # 16000 帧/秒 × 2 字节


@pytest.fixture
def media_dir():
    d = tempfile.mkdtemp(prefix="media-test-")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", *args, "-y"],
        check=True, capture_output=True, timeout=120,
    )


def mk_video(d: str, seconds: float = 2.0, with_audio: bool = True) -> str:
    p = os.path.join(d, "v.mp4")
    if with_audio:
        _ffmpeg("-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                "-f", "lavfi", "-i", f"color=c=blue:s=320x240:d={seconds}",
                "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", p)
    else:
        _ffmpeg("-f", "lavfi", "-i", f"color=c=red:s=160x120:d={seconds}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", p)
    return p


def mk_audio(d: str, ext: str, seconds: float = 1.0) -> str:
    p = os.path.join(d, f"a.{ext}")
    _ffmpeg("-f", "lavfi", "-i", f"sine=frequency=880:duration={seconds}", p)
    return p


# ── 抽音轨 ──────────────────────────────────────────────────────────────────


@needs_ffmpeg
def test_video_audio_track_is_extracted(media_dir):
    """视频抽出来的时长要对得上 —— 对不上说明命令参数错了（比如漏了 -vn
    会把视频流也当数据）。"""
    pcm = D._ffmpeg_to_pcm16k(mk_video(media_dir, seconds=2.0))
    assert abs(len(pcm) / BYTES_PER_SEC - 2.0) < 0.2, f"{len(pcm)} 字节"


@needs_ffmpeg
def test_extracted_pcm_is_raw(media_dir):
    """基座要裸 PCM。带 RIFF 头会被当成音频数据解，识别结果是乱码。"""
    pcm = D._ffmpeg_to_pcm16k(mk_video(media_dir))
    assert not pcm.startswith(b"RIFF")
    assert not pcm.startswith(b"\x00\x00\x00")  # 不是 mp4 的 ftyp 盒


@needs_ffmpeg
def test_extracted_pcm_is_not_silence(media_dir):
    """正弦波抽出来必须有非零样本。全零说明抽的是空轨。"""
    assert any(D._ffmpeg_to_pcm16k(mk_video(media_dir))[:4000])


@needs_ffmpeg
@pytest.mark.parametrize("ext", ["mp3", "flac", "ogg", "wav"])
def test_lossy_audio_formats_work(media_dir, ext):
    """标准库的 wave 只认 WAV。这几个格式以前读不了。"""
    pcm = D._ffmpeg_to_pcm16k(mk_audio(media_dir, ext, seconds=1.0))
    assert abs(len(pcm) / BYTES_PER_SEC - 1.0) < 0.2


@needs_ffmpeg
def test_video_without_audio_raises(media_dir):
    """无音轨的视频必须抛。返回空会被当成「这段没内容」，文档状态变 ready，
    用户看不出问题 —— 这是这个库里反复出现的那类静默失败。
    """
    with pytest.raises(RuntimeError, match="ffmpeg"):
        D._ffmpeg_to_pcm16k(mk_video(media_dir, with_audio=False))


@needs_ffmpeg
def test_corrupt_file_raises(media_dir):
    p = os.path.join(media_dir, "broken.mp4")
    Path(p).write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\xff" * 200)
    with pytest.raises(RuntimeError):
        D._ffmpeg_to_pcm16k(p)


# ── 统一入口的分派 ──────────────────────────────────────────────────────────


@needs_ffmpeg
def test_unified_entry_uses_ffmpeg_when_available(media_dir):
    mp4 = mk_video(media_dir)
    assert D._to_pcm16k(mp4) == D._ffmpeg_to_pcm16k(mp4)


def test_unified_entry_falls_back_to_stdlib(monkeypatch, media_dir):
    """没有 ffmpeg 时退回标准库。本地开发和 CI 环境不一定装了 ffmpeg，
    退路必须在 —— 否则那些环境里连 WAV 都处理不了。
    """
    import wave

    p = os.path.join(media_dir, "s.wav")
    with wave.open(p, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x40" * BYTES_PER_SEC)

    monkeypatch.setattr(D, "_has_ffmpeg", staticmethod(lambda: False))
    called = []
    monkeypatch.setattr(
        D, "_ffmpeg_to_pcm16k",
        staticmethod(lambda fp: called.append(fp) or b""),
    )
    pcm = D._to_pcm16k(p)
    assert not called, "没有 ffmpeg 却去调它了"
    assert abs(len(pcm) - BYTES_PER_SEC) < 400


# ── 格式集合与白名单 ────────────────────────────────────────────────────────


def test_media_exts_shrink_without_ffmpeg(monkeypatch):
    """白名单必须跟着运行时能力变。写死的后果：没 ffmpeg 时 mp4 能上传但
    解析必失败（上传成功但搜不到）。
    """
    monkeypatch.setattr(P, "_has_ffmpeg", lambda: False)
    assert P.media_exts() == frozenset({"wav"})

    monkeypatch.setattr(P, "_has_ffmpeg", lambda: True)
    with_ff = P.media_exts()
    assert "mp4" in with_ff and "mp3" in with_ff
    assert len(with_ff) > 10


def test_video_enters_whitelist_only_with_ffmpeg(monkeypatch):
    monkeypatch.delenv("MINERU_URL", raising=False)
    monkeypatch.setattr(P, "_has_ffmpeg", lambda: False)
    assert "mp4" not in P.supported_upload_exts()

    monkeypatch.setattr(P, "_has_ffmpeg", lambda: True)
    assert "mp4" in P.supported_upload_exts()


def test_video_and_audio_share_one_parser(monkeypatch, media_dir):
    """同一条路：parse_audio 同时服务两者，没有 parse_video。

    钉这个是因为「视频单独一条路」是很自然的直觉，而那会多出一份重复实现 ——
    ffmpeg 对 mp4 和 mp3 本来就是同一个命令。
    """
    monkeypatch.setattr(P, "_has_ffmpeg", lambda: True)
    seen = []
    monkeypatch.setattr(P, "parse_audio", lambda p: seen.append(p) or {})
    monkeypatch.setattr(
        P, "parse_pdf_mineru", lambda p: pytest.fail("音视频不该进 MinerU")
    )

    P.parse_document("/tmp/x.mp4")
    P.parse_document("/tmp/x.mp3")
    P.parse_document("/tmp/x.wav")
    assert len(seen) == 3
    assert not hasattr(P, "parse_video"), "多出了一份重复实现"


def test_parse_audio_returns_file_path_for_both(media_dir):
    """parse_audio 只交出路径，转写在 kb_processor 里做（那里才有 db session
    去查 ASR 模型配置）。视频也走这个结构。"""
    p = os.path.join(media_dir, "x.mp4")
    Path(p).write_bytes(b"fake")
    out = P.parse_audio(p)
    assert out["audio_items"][0]["file_path"] == p
    assert out["text_chunks"] == []
