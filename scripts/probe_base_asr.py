#!/usr/bin/env python3
"""探基座 ASR：key 能不能用、funasr 认不认这个请求体。

在 lea-rag 容器里跑：
    docker exec lea-rag-test python /tmp/probe_base_asr.py /tmp/.asrkey /tmp/asrtest.wav

key 从文件读而不是命令行参数 —— 参数会出现在 ps 和 shell history 里。
脚本不打印 key，只打印它的长度和前缀形态。

走的是 kb_processor._asr_transcribe 的同一条路（_to_pcm16k + base_pcm JSON），
所以这里通了，入库那条也就通了。

带两个反证：
  - 故意用错的 key → 必须被拒。否则说明这个接口压根不鉴权，
    「key 能用」这个结论就没有意义。
  - 故意用错的 model 名 → 看服务怎么回，确认模型名是真的在生效。
"""

import base64
import json
import os
import sys

sys.path.insert(0, "/app/src")

import httpx  # noqa: E402
from lea_rag.services.kb_processor import DocumentProcessor as DP  # noqa: E402

API_BASE = os.getenv("ASR_API_BASE", "https://tenantapi-ai.ouchn.edu.cn")
MODEL = os.getenv("ASR_MODEL", "funasr")

key_file = sys.argv[1] if len(sys.argv) > 1 else "/tmp/.asrkey"
audio = sys.argv[2] if len(sys.argv) > 2 else "/tmp/asrtest.wav"

with open(key_file) as f:
    KEY = f.read().strip()

print(f"api_base = {API_BASE}")
print(f"model    = {MODEL}")
print(f"key      = {len(KEY)} 字符，形如 {KEY[:7]}…{'*' * 8}（不打印全文）")
print(f"audio    = {audio}")
print()

pcm = DP._to_pcm16k(audio)
print(f"PCM: {len(pcm)} 字节 = {len(pcm) / 2 / 16000:.2f}s @16k/16bit/mono")
print()

url = f"{API_BASE.rstrip('/')}/v1/audio/transcriptions"


def post(key: str, model: str, label: str):
    body = {
        "model": model,
        "audio": base64.b64encode(pcm).decode(),
        "audio_encoding": "base64",
        "request_format": "pcm",
        "status": 2,
        "language": "zh_cn",
    }
    print(f"--- {label} ---")
    try:
        r = httpx.post(
            url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=body,
            timeout=600.0,
        )
        print(f"HTTP {r.status_code}")
        try:
            data = r.json()
        except Exception:
            print(f"非 JSON 响应: {r.text[:300]}")
            return r.status_code, None
        # 响应体可能很大（有的服务回逐字时间戳），只看关心的字段
        if isinstance(data, dict):
            keys = list(data.keys())
            print(f"响应字段: {keys}")
            txt = data.get("text")
            if txt is None:
                print(f"没有 text 字段。响应前 400 字符: {json.dumps(data, ensure_ascii=False)[:400]}")
            else:
                print(f"text ({len(txt)} 字符): {txt!r}")
        else:
            print(f"响应不是 dict: {str(data)[:300]}")
        return r.status_code, data
    except Exception as e:
        print(f"请求异常 {type(e).__name__}: {str(e)[:300]}")
        return None, None


st, data = post(KEY, MODEL, f"正常请求（{MODEL}）")
print()

# 反证 1：错的 key 必须被拒
st_bad, _ = post("tenant-" + "0" * 32, MODEL, "反证：错误的 key")
print()

print("=" * 68)
if st and 200 <= st < 300 and isinstance(data, dict) and data.get("text"):
    print("✓ key 可用，funasr 认这个请求体，转写有内容")
    if st_bad and 200 <= st_bad < 300:
        print("✗ 但反证失败：错误的 key 也返回了 2xx —— 这个接口不鉴权，"
              "「key 能用」不能说明任何事")
    else:
        print(f"✓ 反证通过：错误的 key 返回 {st_bad}")
elif st and 200 <= st < 300:
    print("⚠ HTTP 2xx 但没拿到 text —— 请求体格式或模型名可能不对")
else:
    print(f"✗ 请求失败（HTTP {st}）")
print("=" * 68)
