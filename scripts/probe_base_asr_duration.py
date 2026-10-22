#!/usr/bin/env python3
"""spark-asr 的时长上限到底是多少。

文档说"默认模型支持 60s 内音频，funasr 支持 3 小时以上"。而 funasr 稳定
500「接收消息超时」（3/3），spark-asr 12.25s 完美（10/10 关键词）。

知识库里的视频基本都超 60s，所以这条限制决定下一步：
  - 真有 60s 限制 → 要在 kb_processor 里分段转写再拼接
  - 没有限制 → 直接用 spark-asr

不信文档，实测。PCM 是未压缩的，所以把同一段音频在字节层面重复 N 次就得到
N 倍时长，内容已知，转写质量可以按关键词重复次数判断。

阶梯：12s → 30s → 59s → 61s → 90s → 180s，一旦失败就停，失败点即上限。
"""

import base64
import os
import sys
import time

sys.path.insert(0, "/app/src")

import httpx  # noqa: E402
from lea_rag.services.kb_processor import DocumentProcessor as DP  # noqa: E402

API_BASE = os.getenv("ASR_API_BASE", "https://tenantapi-ai.ouchn.edu.cn")
URL = f"{API_BASE.rstrip('/')}/v1/audio/transcriptions"
MODEL = os.getenv("ASR_MODEL", "spark-asr")

key_file = sys.argv[1] if len(sys.argv) > 1 else "/tmp/.asrkey"
audio = sys.argv[2] if len(sys.argv) > 2 else "/tmp/asrtest.wav"
with open(key_file) as f:
    KEY = f.read().strip()

HDR = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
BPS = 16000 * 2  # 16kHz × 16bit mono = 32000 字节/秒

unit = DP._to_pcm16k(audio)
unit_secs = len(unit) / BPS
print(f"单元音频 {unit_secs:.2f}s，{len(unit)} 字节")
print(f"模型 {MODEL}")
print()


def make(secs: float) -> bytes:
    """重复拼到指定时长。PCM 未压缩，拼接即延长。"""
    need = int(secs * BPS)
    reps = need // len(unit) + 1
    return (unit * reps)[:need]


def transcribe(data: bytes, timeout: float = 900.0):
    body = {
        "model": MODEL,
        "audio": base64.b64encode(data).decode(),
        "audio_encoding": "raw",
        "request_format": "pcm",
        "status": 2,
        "language": "zh_cn",
    }
    t0 = time.time()
    try:
        r = httpx.post(URL, headers=HDR, json=body, timeout=timeout)
    except Exception as e:
        return None, None, time.time() - t0, f"{type(e).__name__}: {str(e)[:150]}"
    dt = time.time() - t0
    try:
        d = r.json()
    except Exception:
        return r.status_code, None, dt, r.text[:200]
    if isinstance(d, dict) and d.get("text"):
        return r.status_code, d["text"], dt, ""
    return r.status_code, None, dt, str(d)[:200]


print("=" * 72)
print("时长阶梯")
print("=" * 72)
limit = None
for secs in (12, 30, 59, 61, 90, 180, 600):
    data = make(secs)
    mb = len(base64.b64encode(data)) / 1024 / 1024
    st, txt, dt, err = transcribe(data)
    if txt:
        # "知识库"出现几次 ≈ 转写覆盖了几个重复单元
        n = txt.count("知识库")
        expect = max(1, int(secs / unit_secs))
        print(f"  ✓ {secs:>3}s (payload {mb:.1f}MB) HTTP {st} {dt:>5.1f}s "
              f"text {len(txt)} 字符，'知识库' {n} 次（预期约 {expect}）")
        if n < expect * 0.5:
            print(f"      ⚠ 只转了前面一部分 —— 实际上限可能在 {secs}s 以下")
    else:
        print(f"  ✗ {secs:>3}s (payload {mb:.1f}MB) HTTP {st} {dt:>5.1f}s  {err[:120]}")
        limit = secs
        break
    time.sleep(2)

print()
print("=" * 72)
if limit:
    print(f"上限在 {limit}s 以下 —— 入库要分段转写")
else:
    print("测到的时长都通，没碰到上限")
print("=" * 72)
