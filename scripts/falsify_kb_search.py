#!/usr/bin/env python3
"""反证格式矩阵的检索判定：首条必须是对的那份，且判定要能红。

矩阵脚本判的是"doc_id 出现在前 5 条里"。11 份文档、top_k=5，这是弱判定 ——
而且每次首条分数都是 0.032786883（= 2/61，RRF 在两路都排第一），看不出
首条到底是哪份。

这里钉三件事：

  1. 搜短语 A，**首条**必须是 doc A（不只是"在前 5 里"）
  2. 交叉检验：搜短语 A 时 doc B 不该排第一 —— 否则说明检索没在按内容区分
  3. 判定能红：拿 doc A 的短语去断言 doc B，必须失败

第 3 条是这个脚本存在的理由。这一整轮我写过三条"永远为真"的判定
（status==ready、score 字符串比较、后 6 行里有 raise），所以新判定都要先
证明它会红。
"""

import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, "/app/src")

FIX = sys.argv[1] if len(sys.argv) > 1 else "/tmp/fmt"
KB_ID = int(sys.argv[2]) if len(sys.argv) > 2 else 0
BASE = "http://127.0.0.1:8300"
TENANT = "verify-fmt"

ok = True


def bad(m: str) -> None:
    global ok
    ok = False
    print(f"  ✗ {m}")


def good(m: str) -> None:
    print(f"  ✓ {m}")


def search(query: str, kb_id: int, top_k: int = 5, doc_ids: list[int] | None = None):
    body = {"query": query, "kb_ids": [kb_id], "top_k": top_k, "tenant_id": TENANT}
    if doc_ids:
        body["document_ids"] = doc_ids
    req = urllib.request.Request(
        f"{BASE}/kb/search", data=json.dumps(body).encode(), method="POST",
        headers={
            "X-Internal-Token": os.getenv("LEA_INTERNAL_TOKEN", ""),
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, json.loads(r.read().decode() or "[]")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]


def docs_of(kb_id: int) -> dict[str, int]:
    """filename → document_id"""
    from lea_rag.db.database import SessionLocal
    from sqlalchemy import text

    db = SessionLocal()
    try:
        rows = db.execute(
            text("SELECT filename, id FROM kb_documents WHERE kb_id=:k"), {"k": kb_id}
        ).fetchall()
        return {r[0]: r[1] for r in rows}
    finally:
        db.close()


if not KB_ID:
    # 没给就取最近那个 fmt-matrix 库
    from lea_rag.db.database import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    KB_ID = db.execute(
        text("SELECT id FROM knowledge_bases WHERE name LIKE 'fmt-matrix-%' ORDER BY id DESC LIMIT 1")
    ).scalar()
    db.close()
    print(f"（未指定 kb_id，用最近的 fmt-matrix 库：{KB_ID}）")

manifest = json.load(open(os.path.join(FIX, "manifest.json"), encoding="utf-8"))
PHRASES: dict[str, str] = manifest["phrases"]
FILES: dict[str, str] = manifest["files"]
by_name = docs_of(KB_ID)
print(f"库 {KB_ID} 里 {len(by_name)} 份文档")
print()

# 只看有唯一短语的文本类格式（音视频三份内容相同，不适合排序检验）
cases: list[tuple[str, str, int]] = []
for ext, phrase in PHRASES.items():
    fn = FILES.get(ext)
    if not fn or fn not in by_name or ext in ("wav", "mp3", "mp4"):
        continue
    cases.append((ext, phrase, by_name[fn]))

print("=" * 70)
print("1. 首条必须是对的那份")
print("=" * 70)
tops: dict[str, tuple[str, int]] = {}
for ext, phrase, doc_id in cases:
    st, res = search(phrase, KB_ID)
    if st != 200 or not isinstance(res, list) or not res:
        bad(f"[{ext}] 搜「{phrase}」HTTP {st} 返回 {len(res) if isinstance(res, list) else res}")
        continue
    top_doc = str(res[0].get("document_id"))
    tops[ext] = (phrase, doc_id)
    if top_doc == str(doc_id):
        good(f"[{ext}] 「{phrase}」首条 = doc {doc_id}，正确"
             f"（score={res[0].get('score')}）")
    else:
        bad(f"[{ext}] 「{phrase}」首条是 doc {top_doc}，应该是 {doc_id}。"
            f"前 3 条：{[(r.get('document_id'), r.get('content', '')[:25]) for r in res[:3]]}")

print()
print("=" * 70)
print("2. 交叉检验：A 的短语不该让 B 排第一")
print("=" * 70)
exts = [e for e in tops]
cross_ok = 0
cross_bad = 0
for i, ea in enumerate(exts):
    phrase_a, doc_a = tops[ea]
    eb = exts[(i + 1) % len(exts)]
    _, doc_b = tops[eb]
    if doc_a == doc_b:
        continue
    st, res = search(phrase_a, KB_ID)
    if st != 200 or not isinstance(res, list) or not res:
        continue
    top_doc = str(res[0].get("document_id"))
    if top_doc == str(doc_b):
        cross_bad += 1
        bad(f"搜 {ea} 的「{phrase_a}」首条却是 {eb} 的 doc {doc_b}")
    else:
        cross_ok += 1
if cross_bad == 0:
    good(f"{cross_ok} 组交叉检验全部正确：各自的短语不会命中别人")

print()
print("=" * 70)
print("3. 判定能红吗 —— 故意用错的期望值")
print("=" * 70)
if len(exts) >= 2:
    ea, eb = exts[0], exts[1]
    phrase_a, doc_a = tops[ea]
    _, doc_b = tops[eb]
    st, res = search(phrase_a, KB_ID)
    top_doc = str(res[0].get("document_id")) if isinstance(res, list) and res else None
    # 故意断言首条是 doc_b（错的）
    if top_doc == str(doc_b):
        bad("反证失败：错的期望值居然成立 —— 判定区分不出文档")
    else:
        good(f"反证通过：断言「{phrase_a}」首条是 doc {doc_b}（错的）会失败，"
             f"实际首条是 {top_doc}（= 正确的 {doc_a}）")

    # 再来一个：搜一个任何文档都没有的短语，看它返回什么
    st, res = search("鸮羽琉璃哨音八十四", KB_ID)
    n = len(res) if isinstance(res, list) else 0
    print()
    print(f"  补充观察：搜库里不存在的短语返回 {n} 条")
    if n:
        print(f"    首条 score={res[0].get('score')}，内容 {res[0].get('content', '')[:50]!r}")
        print("    （语义检索总会返回最近邻，不为空是正常的 —— 所以"
              "「返回了东西」本身不能当成功判据，必须看是不是对的那份）")

print()
print("=" * 70)
print("PASS" if ok else "FAIL")
print("=" * 70)
sys.exit(0 if ok else 1)
