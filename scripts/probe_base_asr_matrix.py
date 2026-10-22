#!/usr/bin/env python3
"""基座 ASR 请求体矩阵探测。

单发 status=2 得到 HTTP 500「接收消息超时」—— 鉴权过了（错 key 是 401
「秘钥无效」），是服务端在等后续消息。两个嫌疑：

  1. status 是分帧语义（0 首帧 / 1 中间 / 2 末帧），只发 2 等于直接交末帧，
     会话没建立过。
  2. audio_encoding 描述的是**编解码**（raw/speex/lame），不是传输编码。
     填 "base64" 可能让服务去找一个叫 base64 的编解码器。

先用 2 秒短音频跑矩阵找通的组合（短的快，且能顺带区分"是不是体积问题"），
找到之后再用 12 秒的复测。

每个组合都打印 HTTP 码和响应，不做任何"看起来像成功"的推断 ——
判定只认 text 字段里有字。
"""

import base64
import json
import os
import sys
import time

sys.path.insert(0, "/app/src")

import httpx  # noqa: E402
from lea_rag.services.kb_processor import DocumentProcessor as DP  # noqa: E402

API_BASE = os.getenv("ASR_API_BASE", "https://tenantapi-ai.ouchn.edu.cn")
MODEL = os.getenv("ASR_MODEL", "funasr")
URL = f"{API_BASE.rstrip('/')}/v1/audio/transcriptions"

key_file = sys.argv[1] if len(sys.argv) > 1 else "/tmp/.asrkey"
audio = sys.argv[2] if len(sys.argv) > 2 else "/tmp/asrtest.wav"
with open(key_file) as f:
    KEY = f.read().strip()

full = DP._to_pcm16k(audio)
short = full[: 16000 * 2 * 2]  # 2 秒
print(f"全长 {len(full) / 2 / 16000:.2f}s，短片 {len(short) / 2 / 16000:.2f}s")
print(f"URL  {URL}")
print(f"key  {len(KEY)} 字符，形如 {KEY[:7]}…")
print()

HDR = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}


def shot(body: dict, label: str, timeout: float = 120.0):
    """发一个请求，返回 (status, text或None, 摘要)。"""
    try:
        r = httpx.post(URL, headers=HDR, json=body, timeout=timeout)
    except Exception as e:
        return None, None, f"{type(e).__name__}: {str(e)[:120]}"
    try:
        data = r.json()
    except Exception:
        return r.status_code, None, f"非JSON: {r.text[:120]}"
    if isinstance(data, dict):
        txt = data.get("text")
        if txt:
            return r.status_code, txt, f"字段{list(data.keys())}"
        return r.status_code, None, json.dumps(data, ensure_ascii=False)[:200]
    return r.status_code, None, str(data)[:150]


def base(pcm: bytes, **over) -> dict:
    b = {
        "model": MODEL,
        "audio": base64.b64encode(pcm).decode(),
        "audio_encoding": "base64",
        "request_format": "pcm",
        "status": 2,
        "language": "zh_cn",
    }
    b.update(over)
    for k, v in list(b.items()):
        if v is _OMIT:
            del b[k]
    return b


class _Omit:
    pass


_OMIT = _Omit()

CASES = [
    # (标签, 覆盖项)
    ("原样（status=2, enc=base64）", {}),
    ("status=0（首帧）", {"status": 0}),
    ("status 省略", {"status": _OMIT}),
    ("enc=raw", {"audio_encoding": "raw"}),
    ("enc=raw + status=0", {"audio_encoding": "raw", "status": 0}),
    ("enc 省略", {"audio_encoding": _OMIT}),
    ("request_format=audio/L16;rate=16000", {"request_format": "audio/L16;rate=16000"}),
    ("request_format=raw", {"request_format": "raw"}),
    ("request_format 省略", {"request_format": _OMIT}),
    ("只留 model+audio", {
        "audio_encoding": _OMIT, "request_format": _OMIT,
        "status": _OMIT, "language": _OMIT,
    }),
]

winners = []
print("=" * 72)
print("矩阵（2 秒短片）")
print("=" * 72)
for label, over in CASES:
    st, txt, info = shot(base(short, **over), label)
    flag = "✓" if txt else " "
    print(f"{flag} [{st}] {label}")
    print(f"      {info[:180]}")
    if txt:
        print(f"      text = {txt!r}")
        winners.append((label, over))
    time.sleep(0.5)

print()
print("=" * 72)
if not winners:
    print("短片全军覆没 —— 再试三帧序列（0 → 1 → 2），看是不是真要分帧")
    print("=" * 72)
    n = len(short) // 3 // 2 * 2
    frames = [short[:n], short[n:2 * n], short[2 * n:]]
    for i, (stt, chunk) in enumerate(zip((0, 1, 2), frames)):
        st, txt, info = shot(base(chunk, status=stt), f"帧{i} status={stt}")
        print(f"  帧{i} status={stt}: [{st}] {info[:160]}")
        if txt:
            print(f"    text = {txt!r}")
            winners.append((f"三帧序列 status={stt}", {"framed": True}))
        time.sleep(0.3)
else:
    print(f"通了 {len(winners)} 个组合，用全长 {len(full) / 2 / 16000:.2f}s 复测第一个")
    print("=" * 72)
    label, over = winners[0]
    st, txt, info = shot(base(full, **over), label, timeout=600.0)
    print(f"[{st}] {label}")
    print(f"  {info[:200]}")
    if txt:
        print(f"  text ({len(txt)} 字符) = {txt!r}")
        print("\n✓ 全长也通")
    else:
        print("\n⚠ 短片通但全长不通 —— 是体积/时长限制，不是协议问题")

print()
print("=" * 72)
print("结论：" + ("、".join(w[0] for w in winners) if winners else "没有可用组合"))
print("=" * 72)
