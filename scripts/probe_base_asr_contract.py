#!/usr/bin/env python3
"""让基座 ASR 自己说出请求契约。

矩阵探测的副产品：省掉 audio_encoding 时返回的不是 500，而是

    400 {"errors": {"AudioEncoding": ["The AudioEncoding field is required."]}}

这是 ASP.NET Core 的模型校验错误。意味着服务端在做 DTO 绑定 —— 那就可以让它
自己把契约吐出来，不用猜：

  1. 发空 body `{}` → 一次列出**所有**必填字段（我可能漏了某个）
  2. 给枚举字段填非法值 → 错误信息里常带允许值
  3. 换模型名 → 区分"协议不对"还是"funasr 这个后端挂了"

500「接收消息超时」是过了校验之后在服务内部发生的，消息像是网关在等上游
流式引擎。所以要先确认契约完整，再判断是不是上游的问题。
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
URL = f"{API_BASE.rstrip('/')}/v1/audio/transcriptions"

key_file = sys.argv[1] if len(sys.argv) > 1 else "/tmp/.asrkey"
audio = sys.argv[2] if len(sys.argv) > 2 else "/tmp/asrtest.wav"
with open(key_file) as f:
    KEY = f.read().strip()

HDR = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
pcm = DP._to_pcm16k(audio)[: 16000 * 2]  # 1 秒就够，这里探的是契约不是效果
B64 = base64.b64encode(pcm).decode()


def shot(body, label, timeout=90.0):
    try:
        r = httpx.post(URL, headers=HDR, json=body, timeout=timeout)
    except Exception as e:
        print(f"  [{label}] 异常 {type(e).__name__}: {str(e)[:120]}")
        return None, None
    try:
        data = r.json()
        pretty = json.dumps(data, ensure_ascii=False)
    except Exception:
        data, pretty = None, r.text[:300]
    print(f"  [{r.status_code}] {label}")
    print(f"       {pretty[:400]}")
    return r.status_code, data


print("=" * 72)
print("1. 空 body —— 让它列出所有必填字段")
print("=" * 72)
st, data = shot({}, "空 {}")
required = []
if isinstance(data, dict) and isinstance(data.get("errors"), dict):
    required = sorted(data["errors"].keys())
    print()
    print(f"  必填字段共 {len(required)} 个: {required}")

print()
print("=" * 72)
print("2. 只缺一个字段 —— 确认哪些是真必填")
print("=" * 72)
FULL = {
    "model": "funasr",
    "audio": B64,
    "audio_encoding": "raw",
    "request_format": "pcm",
    "status": 2,
    "language": "zh_cn",
}
for k in list(FULL):
    body = {kk: vv for kk, vv in FULL.items() if kk != k}
    st, data = shot(body, f"去掉 {k}")
    time.sleep(0.3)

print()
print("=" * 72)
print("3. 非法枚举值 —— 错误信息里可能带允许值")
print("=" * 72)
for field, bad in [
    ("audio_encoding", "zzz_not_a_codec"),
    ("request_format", "zzz_not_a_format"),
    ("language", "zz_ZZ"),
    ("status", 99),
]:
    shot({**FULL, field: bad}, f"{field}={bad!r}")
    time.sleep(0.3)

print()
print("=" * 72)
print("4. 换模型 —— 区分协议问题和 funasr 后端问题")
print("=" * 72)
for m in ("spark-asr", "spark-speech", "spark-mulspeech", "funasr", "no-such-model-xyz"):
    shot({**FULL, "model": m}, f"model={m}", timeout=120.0)
    time.sleep(0.3)

print()
print("=" * 72)
print("5. 空音频 —— 看 500 是不是跟音频内容无关")
print("=" * 72)
shot({**FULL, "audio": ""}, "audio=''")
shot({**FULL, "audio": base64.b64encode(b"\x00" * 3200).decode()}, "audio=0.1s 静音")

print()
print("=" * 72)
print("必填字段: " + (", ".join(required) if required else "（空 body 没返回字段列表）"))
print("=" * 72)
