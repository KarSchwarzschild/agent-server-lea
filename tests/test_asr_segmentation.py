"""ASR 分段转写：长音频必须切段，分段失败必须抛。

## 为什么要分段

基座 spark-asr 对长音频**静默截断**。实测（scripts/probe_base_asr_duration.py
和 probe_base_asr_cap.py）：

      59s → 232 字符，5/4  单元   完整
      90s → 346 字符，8/7  单元   完整
     120s → 464 字符，10/9 单元   完整
     135s → 462 字符，10/11 单元  开始缺
     150s → 464 字符，10/12 单元  截断
     180s → 464 字符，10/14 单元  截断
     600s → 462 字符，10/48 单元  截断（只转了前 1/5）

180s 和 600s 返回几乎等长的文本，HTTP 全是 200，没有任何错误字段。

是输入时长封顶而不是输出字符封顶 —— 同样 150s 输入，慢速(0.75x)出 292 字符、
正常 464、快速(1.5x)697。字符数差一倍多，所以封的不是字符。

funasr 号称支持 3 小时，但当前稳定 500「接收消息超时」（连打 3 次全失败），
靠不住。

## 这个文件钉的

  - 超过 segment_seconds 的音频被切成多段，每段单独提交
  - 切分按偶数字节对齐（16bit 采样不能劈半）
  - 转写结果按顺序拼接
  - **任一段失败就抛**，不返回残缺文本
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "packages" / "lea-rag" / "src")
)

from lea_rag.services.kb_processor import DocumentProcessor as DP  # noqa: E402

BPS = 16000 * 2  # 32000 字节/秒


class _Model:
    def __init__(self, **cfg):
        self.name = "spark-asr"
        self.model_name = "spark-asr"
        self.api_base = "https://example.invalid"
        self.api_key = "k"
        self.config = cfg


def _pcm(seconds: float) -> bytes:
    return b"\x11\x22" * int(seconds * 16000)


# ── 切分本身 ────────────────────────────────────────────────────────────────


def test_short_audio_stays_one_segment():
    assert len(DP._split_pcm(_pcm(30), 60)) == 1


@pytest.mark.parametrize(
    "secs,seg,want",
    [
        (60, 60, 1),
        (61, 60, 2),
        (120, 60, 2),
        (121, 60, 3),
        (600, 60, 10),      # 10 分钟讲座 → 10 段
        (3600, 60, 60),     # 1 小时
    ],
)
def test_segment_count(secs, seg, want):
    assert len(DP._split_pcm(_pcm(secs), seg)) == want


def test_segments_are_even_aligned():
    """16bit 采样占 2 字节。切在奇数位会把一个采样劈成两半，后面整段变噪声 ——
    而噪声转写出来是空字符串或乱码，不报错。

    注：这个守卫在**现实取值下触发不了**。32000 字节/秒是偶数，所以 60、30、
    7.3、45.7 这些 int(s*32000) 全是偶数；穷举下来只有 0.0001 这种病态值才产出
    奇数（3 字节）。第一版测试用 seg=7.3，于是把 `//2*2` 去掉也不红 —— 等于没测。

    所以这里用 0.0001 显式走那条边界。不是因为谁会把分段设成 0.1 毫秒，而是
    要让这个守卫在测试里真的承重：去掉它这条就红。
    """
    # 现实取值：怎么切都是偶数，守卫在不在都一样
    for seg in DP._split_pcm(_pcm(125), 7.3):
        assert len(seg) % 2 == 0

    # 病态取值：只有守卫在才是偶数
    segs = DP._split_pcm(_pcm(1), 0.0001)
    assert all(len(s) % 2 == 0 for s in segs), "切点落在 16bit 采样中间"


def test_segments_reassemble_to_original():
    """拼回去必须和原始字节一致 —— 不能丢尾巴。

    `range(0, len, step)` 的最后一段通常比 step 短，写成固定长度切片会丢。
    """
    pcm = _pcm(137)
    assert b"".join(DP._split_pcm(pcm, 60)) == pcm


def test_empty_pcm_does_not_produce_zero_segments():
    """空 PCM 返回一个空段而不是空列表 —— 空列表会让调用方的循环一次不执行，
    于是"转写成功"且结果是空字符串，又一个静默失败。
    """
    assert DP._split_pcm(b"", 60) == [b""]


# ── 转写流程 ────────────────────────────────────────────────────────────────


class _Resp:
    def __init__(self, text="", status=200):
        self._t = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return {"text": self._t}


def test_long_audio_posts_once_per_segment(tmp_path, monkeypatch):
    posts = []

    def fake_post(url, **kw):
        posts.append(kw.get("json", {}))
        return _Resp(f"第{len(posts)}段。")

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(DP, "_to_pcm16k", staticmethod(lambda p: _pcm(185)))

    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")

    out = DP._asr_transcribe(None, _Model(), str(f))

    assert len(posts) == 4, f"185s / 60s 该是 4 段，实际 {len(posts)}"
    assert out == "第1段。第2段。第3段。第4段。", "要按顺序拼接"
    # 每段的 payload 都该比整段小
    for p in posts:
        assert p["status"] == 2
        assert p["model"] == "spark-asr"


def test_segment_seconds_is_configurable(tmp_path, monkeypatch):
    posts = []
    import httpx
    monkeypatch.setattr(httpx, "post", lambda url, **kw: posts.append(1) or _Resp("x"))
    monkeypatch.setattr(DP, "_to_pcm16k", staticmethod(lambda p: _pcm(100)))

    f = tmp_path / "a.wav"
    f.write_bytes(b"x")

    DP._asr_transcribe(None, _Model(segment_seconds=25), str(f))
    assert len(posts) == 4, "100s / 25s = 4 段"


def test_one_failing_segment_raises_not_partial(tmp_path, monkeypatch):
    """第 3 段失败时必须抛。

    返回前两段拼起来的文本是最坏的结果：内容缺了中间一块，而文本本身
    读不出来缺了什么 —— 下游 embedding、检索、引用全部基于残缺内容，
    且文档状态是 ready。
    """
    calls = []

    def fake_post(url, **kw):
        calls.append(1)
        if len(calls) == 3:
            return _Resp(status=500)
        return _Resp("好。")

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(DP, "_to_pcm16k", staticmethod(lambda p: _pcm(300)))

    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")

    with pytest.raises(RuntimeError) as ei:
        DP._asr_transcribe(None, _Model(), str(f))

    msg = str(ei.value)
    assert "3/5" in msg, f"报错要说清是第几段，实际：{msg}"


def test_network_error_on_a_segment_also_raises(tmp_path, monkeypatch):
    import httpx

    def boom(url, **kw):
        raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(httpx, "post", boom)
    monkeypatch.setattr(DP, "_to_pcm16k", staticmethod(lambda p: _pcm(120)))

    f = tmp_path / "a.wav"
    f.write_bytes(b"x")

    with pytest.raises(RuntimeError):
        DP._asr_transcribe(None, _Model(), str(f))


def test_openai_protocol_still_swallows_errors(tmp_path, monkeypatch):
    """openai 分支保持原行为（失败返回空）—— 它不分段，没有"残缺"问题，
    而且改它会影响既有配置。这条是钉住"我只改了 base_pcm 那条路"。
    """
    import httpx
    monkeypatch.setattr(httpx, "post", lambda url, **kw: _Resp(status=500))

    f = tmp_path / "a.wav"
    f.write_bytes(b"x")

    assert DP._asr_transcribe(None, _Model(protocol="openai"), str(f)) == ""


def test_missing_file_returns_empty(tmp_path):
    assert DP._asr_transcribe(None, _Model(), str(tmp_path / "nope.wav")) == ""


def test_audio_encoding_field_is_not_a_safety_net():
    """源码级提醒：audio_encoding 必填但不校验。

    实测填 lame / speex 这种明显错的编解码器，spark-asr 返回的转写一样正确。
    所以它检测不出配置错误。这条钉住注释别被删掉 —— 否则下一个人会以为
    填对了这个字段就说明协议没问题。
    """
    import inspect

    src = inspect.getsource(DP._asr_transcribe)
    assert "不校验" in src, "audio_encoding 不被校验这件事要留在注释里"
