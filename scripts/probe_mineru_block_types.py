#!/usr/bin/env python3
"""实测 MinerU content_list.json 里会出现哪些 type。

在 lea-rag 容器里跑：
    docker exec lea-rag-test python /tmp/probe_mineru_block_types.py <pdf> [...]

## 为什么要探

_parse_content_list 只认四种 type：text / list / table / image。实测发现
MinerU 给饼图的 type 是 **chart**，于是走到 else、被静默丢掉 —— 图在 zip 的
images/ 里躺着，images_dict 也收到了，只是没人去取。

官方文档（opendatalab.github.io/MinerU/reference/output_files/）没有给 type
枚举，真正的定义在 docvortex 包里，而我们只调云端 API 没装那个包。所以靠实测：
把样本喂进去，把每一项的 type 和字段名打出来。

只补 chart 一个是治症状 —— 下一个没覆盖的 type（equation / code / 别的）
还会再丢一次。所以要把完整分布看清楚，再决定按"白名单补齐"还是改成
"按有没有 img_path 判断"。
"""

import io
import json
import os
import sys
import time
import zipfile

sys.path.insert(0, "/app/src")

import httpx  # noqa: E402

TOK = os.getenv("MINERU_TOKEN", "")
H = {"Authorization": f"Bearer {TOK}", "Content-Type": "application/json"}
BATCH = "https://mineru.net/api/v4/file-urls/batch"
POLL = "https://mineru.net/api/v4/extract-results/batch/{}"

files = sys.argv[1:] or ["/tmp/fmt/photopdf.pdf"]

#: 代码里已经处理的 type。其余的都是在被静默丢掉。
HANDLED = {"text", "list", "table", "image"}

all_types: dict[str, int] = {}
img_bearing: dict[str, int] = {}

for path in files:
    name = os.path.basename(path)
    print("=" * 72)
    print(f"{name}")
    print("=" * 72)

    r = httpx.post(BATCH, headers=H, json={
        "enable_formula": True,       # 打开公式，看 equation 会不会出现
        "language": "ch",
        "files": [{"name": name, "is_ocr": False}],
    }, timeout=60)
    d = r.json().get("data") or {}
    bid, urls = d.get("batch_id"), d.get("file_urls") or []
    if not bid or not urls:
        print(f"  申请上传地址失败: {r.text[:200]}")
        continue

    with open(path, "rb") as f:
        httpx.put(urls[0], content=f.read(), timeout=180)

    res = None
    for _ in range(60):
        time.sleep(3)
        rr = httpx.get(POLL.format(bid), headers=H, timeout=60).json()
        lst = ((rr.get("data") or {}).get("extract_result")) or []
        if lst:
            res = lst[0]
            if res.get("state") in ("done", "failed"):
                break
    if not res or res.get("state") != "done":
        print(f"  解析未完成: {res}")
        continue

    z = httpx.get(res["full_zip_url"], timeout=180).content
    with zipfile.ZipFile(io.BytesIO(z)) as zf:
        names = zf.namelist()
        imgs = [n for n in names if "images/" in n and not n.endswith("/")]
        print(f"  zip {len(z):,} B，{len(names)} 项，其中 images/ 下 {len(imgs)} 个文件")

        v1 = [n for n in names if n.endswith("content_list.json")]
        v2 = [n for n in names if n.endswith("content_list_v2.json")]
        print(f"  content_list.json: {len(v1)}，content_list_v2.json: {len(v2)}")

        for n in v1:
            cl = json.loads(zf.read(n))
            print(f"\n  --- {n.split('/')[-1]}（{len(cl)} 项）---")
            for item in cl:
                t = item.get("type", "?")
                all_types[t] = all_types.get(t, 0) + 1
                has_img = bool(item.get("img_path"))
                if has_img:
                    img_bearing[t] = img_bearing.get(t, 0) + 1
                flag = "  " if t in HANDLED else "★"
                keys = sorted(k for k in item if k not in ("type", "page_idx"))
                preview = ""
                if item.get("text"):
                    preview = repr(item["text"][:40])
                elif item.get("img_path"):
                    preview = item["img_path"].split("/")[-1][:30]
                print(f"   {flag} type={t:<10} img_path={'有' if has_img else '无'} "
                      f"字段={keys} {preview}")
    print()

print("=" * 72)
print("汇总")
print("=" * 72)
print(f"  {'type':<12} {'出现':<6} {'带 img_path':<12} {'代码处理了吗'}")
for t, n in sorted(all_types.items(), key=lambda kv: -kv[1]):
    ni = img_bearing.get(t, 0)
    print(f"  {t:<12} {n:<6} {ni:<12} {'是' if t in HANDLED else '否 ★丢掉'}")

missed = [t for t in all_types if t not in HANDLED]
missed_img = [t for t in missed if img_bearing.get(t)]
print()
if missed_img:
    print(f"★ 这些 type 带图但被丢掉: {missed_img}")
    print("  → 判定方式应该改成「有 img_path 就当图片」，而不是枚举 type —— "
          "枚举漏一个就静默丢一类内容")
elif missed:
    print(f"这些 type 没处理但不带图: {missed}（可能丢文本内容）")
else:
    print("这些样本里没有未处理的 type")
