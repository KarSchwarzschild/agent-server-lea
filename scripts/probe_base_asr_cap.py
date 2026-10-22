#!/usr/bin/env python3
"""spark-asr 静默截断的边界在哪 —— 决定分段长度。

阶梯探测的数据：
    59s  → 232 字符 / 5 个单元（完整）
    90s  → 346 字符 / 8 个单元（完整）
   180s  → 464 字符 / 10 个单元（预期 14，少了）
   600s  → 462 字符 / 10 个单元（预期 48，少了一大半）

180s 和 600s 返回几乎等长的文本，说明是**输出封顶**而不是渐进退化。
两个假设：
  A. 封顶在输出字符数（~460 字符）
  B. 封顶在输入时长（~115-120s）

区分方法：用语速不同的音频。同样 150s 输入，如果 A 成立则两者都停在 ~460
字符；如果 B 成立则慢速那条字符数明显更少（因为 150s 里说的字少）。

这里用变速实现语速差异：ffmpeg atempo 把同一段音频加速/减速，
内容不变、时长变，字/秒随之变。
"""

import base64
import os
import subprocess
import sys
import tempfile
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
BPS = 16000 * 2

unit = DP._to_pcm16k(audio)
print(f"单元 {len(unit) / BPS:.2f}s")


def make(secs: float, src: bytes) -> bytes:
    need = int(secs * BPS)
    return (src * (need // len(src) + 1))[:need]


def tempo(src: bytes, rate: float) -> bytes:
    """变速不变调，得到不同语速的同内容音频。

    用 TemporaryDirectory 而不是 mkdtemp —— 后者不会自己删，探针跑几次就在
    容器 /tmp 下留一堆 tmpXXXX/，下次排查磁盘时会被当成生产代码的泄漏。
    """
    with tempfile.TemporaryDirectory() as d:
        raw = os.path.join(d, "in.raw")
        with open(raw, "wb") as f:
            f.write(src)
        p = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
             "-f", "s16le", "-ar", "16000", "-ac", "1", "-i", raw,
             "-filter:a", f"atempo={rate}", "-f", "s16le", "-"],
            capture_output=True, check=True,
        )
        return p.stdout


def ask(data: bytes, timeout: float = 900.0):
    body = {
        "model": MODEL, "audio": base64.b64encode(data).decode(),
        "audio_encoding": "raw", "request_format": "pcm",
        "status": 2, "language": "zh_cn",
    }
    t0 = time.time()
    try:
        r = httpx.post(URL, headers=HDR, json=body, timeout=timeout)
    except Exception as e:
        return None, None, time.time() - t0, f"{type(e).__name__}"
    dt = time.time() - t0
    try:
        d = r.json()
    except Exception:
        return r.status_code, None, dt, r.text[:150]
    return r.status_code, (d.get("text") if isinstance(d, dict) else None), dt, str(d)[:150]


print()
print("=" * 72)
print("1. 卡边界：90 → 180 之间")
print("=" * 72)
rows = []
for secs in (90, 105, 120, 135, 150, 180):
    st, txt, dt, err = ask(make(secs, unit))
    if txt:
        n = txt.count("知识库")
        exp = max(1, int(secs / (len(unit) / BPS)))
        full = n >= exp * 0.9
        print(f"  {secs:>3}s → {len(txt):>3} 字符，单元 {n}/{exp} "
              f"{'完整' if full else '★截断'}  ({dt:.1f}s)")
        rows.append((secs, len(txt), n, exp, full))
    else:
        print(f"  {secs:>3}s → 失败 {err[:80]}")
    time.sleep(1.5)

print()
print("=" * 72)
print("2. 区分「字符封顶」还是「时长封顶」：同为 150s，语速不同")
print("=" * 72)
slow = tempo(unit, 0.75)   # 慢 → 150s 里字更少
fast = tempo(unit, 1.5)    # 快 → 150s 里字更多
print(f"  慢速单元 {len(slow) / BPS:.2f}s，快速单元 {len(fast) / BPS:.2f}s")

for label, src in (("慢速 0.75x", slow), ("快速 1.5x", fast)):
    st, txt, dt, err = ask(make(150, src))
    if txt:
        print(f"  {label} 150s → {len(txt)} 字符，'知识库' {txt.count('知识库')} 次 ({dt:.1f}s)")
    else:
        print(f"  {label} 150s → 失败 {err[:80]}")
    time.sleep(1.5)

print()
print("=" * 72)
cut = [r for r in rows if not r[4]]
if cut:
    first = cut[0][0]
    ok = [r for r in rows if r[4]]
    last_ok = ok[-1][0] if ok else 0
    print(f"截断起点在 {last_ok}s 和 {first}s 之间。")
    print("分段长度取 60s（留一倍余量），不贴着边界走。")
else:
    print("这一段阶梯没测出截断点，需要往上加。")
print("=" * 72)
