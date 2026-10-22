#!/usr/bin/env python3
"""试新的 MinerU token，并对比它和 .env 里那个是不是同一个。

在 lea-rag 容器里跑：
    docker exec lea-rag-test python /tmp/probe_mineru_token2.py /tmp/.mnrtok

新 token 和 .env 里现有的那个**形态一致**（都是 sk- 开头、51 字符、不含点号），
所以第一件事是确认它们是不是同一个值 —— 如果是，结果必然还是 401，
换之前就能知道。比对用 sha256 前 12 位，不打印任何明文。

文档（mineru.net/apiManage/docs）说得明确：没有 AK/SK 签名，鉴权就是一个
自己在 API 管理页生成的静态 Bearer token。另外还有一组 **不需要 token** 的
轻量接口 /api/v1/agent/parse/*（按 IP 限流），这里一并探 —— 如果 token 这条
走不通，那组接口是第二条路。
"""

import hashlib
import json
import os
import sys

sys.path.insert(0, "/app/src")

import httpx  # noqa: E402

tok_file = sys.argv[1] if len(sys.argv) > 1 else "/tmp/.mnrtok"
with open(tok_file) as f:
    NEW = f.read().strip()
OLD = os.getenv("MINERU_TOKEN", "")


def fp(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:12] if s else "(空)"


print(f"新 token: {len(NEW)} 字符，形如 {NEW[:6]}…，指纹 {fp(NEW)}")
print(f"旧 token: {len(OLD)} 字符，形如 {OLD[:6]}…，指纹 {fp(OLD)}")
print(f"是同一个吗: {NEW == OLD}")
if NEW == OLD:
    print()
    print("⚠ 两者相同 —— 这就是 .env 里已经在用的那个，必然还是 401。")
    print("  需要的是 mineru.net 控制台「API 管理」页面新生成的 token。")
print()

BATCH = "https://mineru.net/api/v4/file-urls/batch"
TASK = "https://mineru.net/api/v4/extract/task"
AGENT = "https://mineru.net/api/v1/agent/parse/url"
DEMO_PDF = "https://cdn-mineru.openxlab.org.cn/demo/example.pdf"


def shot(label: str, method: str, url: str, headers: dict, body: dict | None = None):
    print(f"--- {label} ---")
    try:
        if method == "GET":
            r = httpx.get(url, headers=headers, timeout=90)
        else:
            r = httpx.post(url, headers=headers, json=body, timeout=90)
        print(f"  HTTP {r.status_code}")
        try:
            d = r.json()
            print(f"  {json.dumps(d, ensure_ascii=False)[:300]}")
            return r.status_code, d
        except Exception:
            print(f"  {r.text[:300]}")
            return r.status_code, None
    except Exception as e:
        print(f"  异常 {type(e).__name__}: {str(e)[:150]}")
        return None, None


JSONH = {"Content-Type": "application/json"}

print("=" * 72)
print("1. 新 token 当 Bearer（文档指定的唯一鉴权方式）")
print("=" * 72)
st1, d1 = shot(
    "POST /api/v4/extract/task",
    "POST", TASK,
    {**JSONH, "Authorization": f"Bearer {NEW}"},
    {"url": DEMO_PDF, "model_version": "vlm"},
)
print()
st2, d2 = shot(
    "POST /api/v4/file-urls/batch（代码实际用的那个）",
    "POST", BATCH,
    {**JSONH, "Authorization": f"Bearer {NEW}"},
    {"enable_formula": False, "language": "ch",
     "files": [{"name": "t.pdf", "is_ocr": False}]},
)

print()
print("=" * 72)
print("2. 不带 Bearer 前缀（文档 A0202 专门提到这个坑）")
print("=" * 72)
shot("裸 token", "POST", BATCH, {**JSONH, "Authorization": NEW},
     {"enable_formula": False, "language": "ch",
      "files": [{"name": "t.pdf", "is_ocr": False}]})

print()
print("=" * 72)
print("3. 免 token 的 agent 接口（第二条路）")
print("=" * 72)
st3, d3 = shot("POST /api/v1/agent/parse/url（文档说无需鉴权）",
               "POST", AGENT, JSONH, {"url": DEMO_PDF})

print()
print("=" * 72)
ok_v4 = (st1 and 200 <= st1 < 300) or (st2 and 200 <= st2 < 300)
ok_agent = st3 and 200 <= st3 < 300
if ok_v4:
    print("✓ 新 token 可用 —— 换进 .env 即可，PDF 恢复走 MinerU")
elif ok_agent:
    print("✗ 新 token 不可用，但 /api/v1/agent/parse/* 能用（免鉴权、按 IP 限流）")
    print("  → 可以改代码走 agent 接口，代价是限流和功能可能更少")
else:
    print("✗ 两条路都不通")
    if st1 == 401 or st2 == 401:
        code = (d1 or d2 or {}).get("msgCode") if isinstance(d1 or d2, dict) else None
        print(f"  v4 返回 401{f'（msgCode={code}）' if code else ''}")
        print("  A0211 = token 过期；A0202 = token 格式/前缀不对")
print("=" * 72)
