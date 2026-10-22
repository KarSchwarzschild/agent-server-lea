#!/usr/bin/env python3
"""MinerU 为什么 401 —— 在线服务在，是凭证类型不对。

在 lea-rag 容器里跑：
    docker exec lea-rag-test python /tmp/probe_mineru_credential.py

线索：MINERU_TOKEN 是 `sk-xxx` 形态、51 字符、不含点号。而 MinerU 官方
API（mineru.net/api/v4）要的是控制台签发的 **JWT**（三段、点号分隔、
通常 700+ 字符）。`sk-` 是 OpenAI 风格的 key。

所以有两种可能：
  A. 有人把中转平台的 key 填进了 MINERU_TOKEN，而 URL 还指向 mineru.net
  B. 这就是个占位值，从来没配过真的 MinerU token

脚本不打印 token，只报状态码和响应体。
"""

import json
import os
import sys

sys.path.insert(0, "/app/src")

import httpx  # noqa: E402

TOKEN = os.getenv("MINERU_TOKEN", "")
print(f"MINERU_TOKEN: {len(TOKEN)} 字符，形如 {TOKEN[:6]}…（不打印全文）")
print(f"  含点号（JWT 特征）: {'.' in TOKEN}")
print(f"  JWT 三段: {len(TOKEN.split('.')) == 3}")
print()

from lea_rag.parsers import parser as P  # noqa: E402

print(f"MINERU_URL（本地模式）: {P.mineru_local_url() or '未配置'}")
print()

CASES = [
    # (标签, URL, 鉴权头)
    (
        "MinerU 官方 v4 + Bearer",
        "https://mineru.net/api/v4/file-urls/batch",
        {"Authorization": f"Bearer {TOKEN}"},
    ),
    (
        "MinerU 官方 v4 + 裸 token",
        "https://mineru.net/api/v4/file-urls/batch",
        {"Authorization": TOKEN},
    ),
    (
        "MinerU 官方 v4 + 无鉴权（看它要什么）",
        "https://mineru.net/api/v4/file-urls/batch",
        {},
    ),
]

BODY = {"enable_formula": False, "language": "ch", "files": [{"name": "t.pdf", "is_ocr": False}]}

for label, url, hdr in CASES:
    print(f"--- {label} ---")
    try:
        r = httpx.post(url, headers={**hdr, "Content-Type": "application/json"},
                       json=BODY, timeout=60)
        print(f"  HTTP {r.status_code}")
        try:
            print(f"  {json.dumps(r.json(), ensure_ascii=False)[:260]}")
        except Exception:
            print(f"  {r.text[:260]}")
    except Exception as e:
        print(f"  异常 {type(e).__name__}: {str(e)[:150]}")
    print()

# 这个 key 在中转平台上好用吗 —— 如果好用，说明是填错了位置
print("=" * 70)
print("这个 key 在中转平台上好用吗（它是不是被填错了位置）")
print("=" * 70)
try:
    r = httpx.post(
        "https://tenantapi-ai.ouchn.edu.cn/v1/embeddings",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        json={"model": "bge-large-zh-v1.5", "input": ["探测"]},
        timeout=60,
    )
    print(f"  中转平台 /v1/embeddings: HTTP {r.status_code}")
    if r.status_code == 200:
        d = r.json()
        n = len(d.get("data", [{}])[0].get("embedding", [])) if d.get("data") else 0
        print(f"  ✓ 这个 key 在中转平台上有效（返回 {n} 维向量）")
        print("  → 结论：它是中转平台的 key，被填进了 MINERU_TOKEN。")
        print("    MinerU 官方 API 不认它，所以每份 PDF 都 401 然后降级。")
    else:
        print(f"  {r.text[:200]}")
        print("  → 这个 key 在中转平台上也不好用，可能就是个占位值")
except Exception as e:
    print(f"  异常 {type(e).__name__}: {str(e)[:150]}")
