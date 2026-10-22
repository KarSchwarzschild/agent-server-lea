#!/usr/bin/env python3
"""确定用哪个 ASR 模型：全长转写效果 + funasr 是否偶发。

契约探测的结论：
  - 协议没问题。spark-asr / spark-speech 用同一个请求体返回 200 + text。
  - funasr 返回 500「接收消息超时」。不是模型名错（错名字会回"模型不存在"），
    是网关认识它但上游引擎不应答。
  - spark-mulspeech 配额用尽。

这里要定两件事：
  1. spark-asr 对 12 秒整段的转写质量（文档说默认模型只支持 60s 内）
  2. funasr 的 500 是偶发还是稳定 —— 连打 3 次

判定看字面覆盖率：源句已知，数转写里命中了多少个关键词。不看"有没有返回"。
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

key_file = sys.argv[1] if len(sys.argv) > 1 else "/tmp/.asrkey"
audio = sys.argv[2] if len(sys.argv) > 2 else "/tmp/asrtest.wav"
with open(key_file) as f:
    KEY = f.read().strip()

HDR = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

# 合成语音的原文，用来算覆盖率
SOURCE = "知识库的多模态处理，文档解析使用 MinerU，视频和音频通过 ffmpeg 抽取音轨之后交给语音识别服务。"
KEYWORDS = ["知识库", "多模态", "文档", "解析", "视频", "音频", "抽取", "音轨", "语音识别", "服务"]

pcm = DP._to_pcm16k(audio)
print(f"音频 {len(pcm) / 2 / 16000:.2f}s @16k/16bit/mono")
print(f"原文 {SOURCE}")
print()


def transcribe(model: str, data: bytes, timeout: float = 600.0):
    body = {
        "model": model,
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
        return None, None, time.time() - t0, f"{type(e).__name__}: {str(e)[:120]}"
    dt = time.time() - t0
    try:
        d = r.json()
    except Exception:
        return r.status_code, None, dt, r.text[:200]
    if isinstance(d, dict) and d.get("text"):
        return r.status_code, d["text"], dt, ""
    return r.status_code, None, dt, str(d)[:200]


def coverage(txt: str) -> tuple[int, list[str]]:
    hit = [k for k in KEYWORDS if k in txt]
    return len(hit), hit


print("=" * 72)
print("1. 全长 12.25s，逐个模型")
print("=" * 72)
results = {}
for m in ("spark-asr", "spark-speech", "funasr"):
    st, txt, dt, err = transcribe(m, pcm)
    print(f"\n  {m}: HTTP {st}  {dt:.1f}s")
    if txt:
        n, hit = coverage(txt)
        print(f"    text ({len(txt)} 字符): {txt}")
        print(f"    关键词 {n}/{len(KEYWORDS)} 命中: {hit}")
        results[m] = (n, txt, dt)
    else:
        print(f"    失败: {err}")
        results[m] = (0, None, dt)
    time.sleep(1)

print()
print("=" * 72)
print("2. funasr 连打 3 次 —— 偶发还是稳定")
print("=" * 72)
short = pcm[: 16000 * 2 * 2]
for i in range(3):
    st, txt, dt, err = transcribe("funasr", short, timeout=120.0)
    print(f"  第 {i + 1} 次: HTTP {st} {dt:.1f}s {txt or err[:90]}")
    time.sleep(1)

print()
print("=" * 72)
best = max(results.items(), key=lambda kv: kv[1][0])
if best[1][0] > 0:
    print(f"推荐 model_name = {best[0]}（关键词 {best[1][0]}/{len(KEYWORDS)}，"
          f"{best[1][2]:.1f}s）")
else:
    print("没有可用模型")
print("=" * 72)
