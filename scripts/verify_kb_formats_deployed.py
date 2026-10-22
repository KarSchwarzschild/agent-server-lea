#!/usr/bin/env python3
"""知识库格式矩阵：每个格式走完整链路，判定到「搜得到」为止。

在 lea-rag 容器里跑：
    docker exec lea-rag-test python /tmp/verify_kb_formats_deployed.py /tmp/fmt

## 判定分四层，前三层都不够

  1. 上传被收/被拒，和预期一致
  2. 文档状态变 ready（不是 error、不是卡在 processing）
  3. chunk 正文里有这份文件的**唯一短语** —— 证明解析对了，而且不是别的文件的内容
  4. 搜这个短语能命中这份文档 —— 证明 embedding + Milvus 索引 + 检索都通了

只做到 2 是空判定：mp4 二进制被切成 138 个乱码 chunk 时状态也是 ready。
只做到 3 也不够：chunk 进了 MySQL 不等于向量进了 Milvus（这个库曾经出现
MySQL 有 23 个 chunk 而 Milvus 0 行，搜索静默返回空）。

## 预期值哪来的

accept/reject 不是我猜的，是从部署态的 supported_upload_exts() 现场读的。
服务器是云端 MinerU 模式（MINERU_URL 没配），所以 xlsx/html/csv/epub 这些
只有本地 MinerU 才能解析的格式**预期被拒**。配上本地 MinerU 后重跑，
它们会自动变成预期接受 —— 这个脚本不用改。
"""

import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, "/app/src")

FIX = sys.argv[1] if len(sys.argv) > 1 else "/tmp/fmt"
BASE = "http://127.0.0.1:8300"
TENANT = "verify-fmt"

ok = True
notes: list[str] = []


def bad(m: str) -> None:
    global ok
    ok = False
    print(f"    ✗ {m}")


def good(m: str) -> None:
    print(f"    ✓ {m}")


def warn(m: str) -> None:
    notes.append(m)
    print(f"    ⚠ {m}")


def _h(extra: dict | None = None) -> dict:
    h = {"X-Internal-Token": os.getenv("LEA_INTERNAL_TOKEN", "")}
    if extra:
        h.update(extra)
    return h


def call(method: str, path: str, body: dict | None = None, timeout: int = 120):
    data = json.dumps(body).encode() if body is not None else None
    hdr = _h({"Content-Type": "application/json"}) if body is not None else _h()
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method, headers=hdr)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def upload(kb_id: int, path: str):
    name = os.path.basename(path)
    with open(path, "rb") as f:
        content = f.read()
    b = "----verifyfmt0123456789"
    body = io.BytesIO()
    body.write(f"--{b}\r\n".encode())
    body.write(f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode())
    body.write(b"Content-Type: application/octet-stream\r\n\r\n")
    body.write(content)
    body.write(f"\r\n--{b}\r\n".encode())
    body.write(b'Content-Disposition: form-data; name="tenant_id"\r\n\r\n')
    body.write(TENANT.encode())
    body.write(f"\r\n--{b}--\r\n".encode())
    req = urllib.request.Request(
        f"{BASE}/kb/{kb_id}/upload", data=body.getvalue(), method="POST",
        headers=_h({"Content-Type": f"multipart/form-data; boundary={b}"}),
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]


def chunks_of(doc_id: int) -> str:
    from lea_rag.db.database import SessionLocal
    from sqlalchemy import text

    db = SessionLocal()
    try:
        rows = db.execute(
            text("SELECT content FROM kb_document_chunks WHERE document_id=:d ORDER BY id"),
            {"d": doc_id},
        ).fetchall()
        return "\n".join(r[0] or "" for r in rows)
    finally:
        db.close()


def _milvus():
    from pymilvus import Collection, connections
    try:
        connections.connect(
            "fmtcheck",
            host=os.getenv("MILVUS_HOST", "milvus"),
            port=os.getenv("MILVUS_PORT", "19530"),
        )
    except Exception:  # noqa: BLE001
        pass
    c = Collection("lea_knowledge_v2", using="fmtcheck")
    c.load()
    return c


def milvus_rows_of(doc_id: int) -> list[dict]:
    """查这份文档在 Milvus 里的行。

    图片必须查这里：它们走 _index_images 直接 insert 成 chunk_type=image_caption，
    **不进 kb_document_chunks**，所以 chunk_count=0 对图片是正常的。
    第一版拿 chunk_count 判图片，于是把正常的 png/jpg 判成了"解析出空内容"。
    """
    try:
        return _milvus().query(
            expr=f"document_id == {doc_id}",
            output_fields=["document_id", "kb_id", "tenant_id", "chunk_type", "content"],
            limit=50,
        )
    except Exception as e:  # noqa: BLE001
        print(f"  （查 Milvus 失败：{type(e).__name__}: {e}）")
        return []


def milvus_count() -> int | None:
    try:
        c = _milvus()
        # 必须先 flush。num_entities 只统计已封闭的 segment，刚插入的在 growing
        # segment 里不计数 —— 第一版没 flush，于是 157→157 看起来"一行没涨"，
        # 而日志里每份文档都有 milvus_inserted count=1。flush 后是 157→167。
        c.flush()
        return c.num_entities
    except Exception as e:  # noqa: BLE001
        print(f"  （读 Milvus 失败：{type(e).__name__}: {e}）")
        return None


# ── 预期值从部署态现场读 ────────────────────────────────────────────────────
from lea_rag.parsers import parser as P  # noqa: E402

SUPPORTED = P.supported_upload_exts()
manifest = json.load(open(os.path.join(FIX, "manifest.json"), encoding="utf-8"))
PHRASES: dict[str, str] = manifest["phrases"]
AUDIO_PHRASES: list[str] = manifest["audio_phrases"]
FILES: dict[str, str] = manifest["files"]

print("=" * 74)
print("部署态解析能力")
print("=" * 74)
print(f"  本地 MinerU: {P.mineru_local_url() or '未配置（云端模式）'}")
print(f"  MinerU 原生格式: {sorted(P.mineru_native_exts())}")
print(f"  ffmpeg: {P._has_ffmpeg()}")
print(f"  上传白名单 {len(SUPPORTED)} 个: {', '.join(sorted(SUPPORTED))}")
print()

st, kb = call("POST", "/kb/create", {
    "name": f"fmt-matrix-{int(time.time())}",
    "tenant_id": TENANT,
    "description": "格式矩阵验证，可删",
})
if st not in (200, 201) or not isinstance(kb, dict):
    print(f"建库失败 {st}: {kb}")
    sys.exit(1)
kb_id = kb["id"]
print(f"测试库 id={kb_id}")

mv_before = milvus_count()
print(f"Milvus 起始行数: {mv_before}")
print()

results: list[tuple] = []
accepted: list[tuple[str, int, str]] = []   # (ext, doc_id, 期望短语)

print("=" * 74)
print("逐格式上传")
print("=" * 74)

for ext in sorted(FILES):
    path = os.path.join(FIX, FILES[ext])
    if not os.path.exists(path):
        continue
    expect_ok = ext in SUPPORTED
    print(f"\n  [{ext}] {FILES[ext]}  预期{'接受' if expect_ok else '拒绝'}")

    st, resp = upload(kb_id, path)
    got_ok = st in (200, 201, 202)

    if got_ok == expect_ok:
        good(f"HTTP {st}，和预期一致")
    elif expect_ok:
        bad(f"HTTP {st} 被拒，但白名单里有 {ext}：{resp}")
    else:
        bad(f"HTTP {st} 被接受，但 {ext} 不在白名单 —— 校验漏了")

    if got_ok and isinstance(resp, dict):
        doc_id = resp.get("id")
        phrase = PHRASES.get(ext)
        if ext in ("wav", "mp3", "mp4"):
            phrase = None   # 音视频用 AUDIO_PHRASES 判定
        accepted.append((ext, doc_id, phrase))
    results.append((ext, expect_ok, got_ok, st))

# ── 等解析 ──────────────────────────────────────────────────────────────────
print()
print("=" * 74)
print("等解析落地（最多 180s）")
print("=" * 74)

pending = {d for _, d, _ in accepted if d}
final: dict[int, dict] = {}
for _ in range(36):
    time.sleep(5)
    st, docs = call("GET", f"/kb/{kb_id}/documents")
    if st == 200 and isinstance(docs, list):
        for d in docs:
            if d.get("id") in pending and d.get("status") in ("ready", "error"):
                final[d["id"]] = d
                pending.discard(d["id"])
    if not pending:
        break

if pending:
    print(f"  ⚠ {len(pending)} 份没在 180s 内落地: {sorted(pending)}")

# ── 逐份查状态 + chunk 正文 ────────────────────────────────────────────────
print()
print("=" * 74)
print("解析结果（状态 + chunk 正文里有没有唯一短语）")
print("=" * 74)

searchable: list[tuple[str, int, str]] = []
for ext, doc_id, phrase in accepted:
    if not doc_id:
        continue
    d = final.get(doc_id)
    print(f"\n  [{ext}] doc={doc_id}")
    if not d:
        bad("没拿到终态")
        continue
    status = d.get("status")
    err = (d.get("error_message") or "")[:200]
    n = d.get("chunk_count") or 0

    if status != "ready":
        bad(f"状态={status} chunks={n} 错误={err}")
        continue

    is_image = ext in ("png", "jpg", "jpeg", "webp", "gif", "bmp")

    if is_image:
        # 图片的正文在 Milvus 的 image_caption 行里，不在 kb_document_chunks。
        # 所以 chunk_count=0 对图片是**正常的** —— 第一版拿它判定，把正常的
        # png/jpg 判成了"解析出空内容"。
        rows = milvus_rows_of(doc_id)
        caps = [r for r in rows if r.get("chunk_type") == "image_caption"]
        if not caps:
            bad(f"Milvus 里没有 image_caption 行（共 {len(rows)} 行）—— 图片没索引")
            continue
        body = "\n".join(r.get("content") or "" for r in caps)
        print(f"    状态=ready，image_caption {len(caps)} 行，描述 {len(body)} 字符")
        print(f"    描述：{body[:150]!r}")
    else:
        if not n:
            bad(f"ready 但 0 chunk —— 解析出空内容（{err}）")
            continue
        body = chunks_of(doc_id)
        print(f"    状态=ready chunks={n}，正文 {len(body)} 字符")

    if phrase:
        if phrase in body:
            good(f"{'描述' if is_image else '正文'}含唯一短语「{phrase}」—— 解析正确")
            searchable.append((ext, doc_id, phrase))
        elif is_image:
            # VLM 认不出图里的字不等于链路坏了。图片这条路的判定底线是
            # "有描述且描述非空"，OCR 准确率是模型能力问题，分开报。
            warn(f"[{ext}] VLM 描述里没有「{phrase}」—— 图片链路通（有描述），"
                 f"但没认出图中文字")
            if body.strip():
                searchable.append((ext, doc_id, "image"))
        else:
            bad(f"正文里找不到「{phrase}」。实际前 150 字符：{body[:150]!r}")
    else:
        hit = [w for w in AUDIO_PHRASES if w in body]
        if len(hit) >= len(AUDIO_PHRASES) * 0.75:
            good(f"转写命中 {len(hit)}/{len(AUDIO_PHRASES)} {hit}")
            searchable.append((ext, doc_id, AUDIO_PHRASES[0]))
        else:
            bad(f"转写只命中 {len(hit)}/{len(AUDIO_PHRASES)} {hit}，正文：{body[:150]!r}")

# ── 检索：这一层才证明向量进了 Milvus ──────────────────────────────────────
mv_after = milvus_count()
print()
print("=" * 74)
print("检索（搜唯一短语，必须命中对应文档）")
print("=" * 74)
print(f"  Milvus: {mv_before} → {mv_after}")
if mv_before is not None and mv_after is not None:
    if mv_after > mv_before:
        good(f"新增 {mv_after - mv_before} 行向量")
    else:
        bad("Milvus 行数没涨 —— chunk 进了 MySQL 但向量没进 Milvus，搜索会静默返空")

#: 唯一短语只属于一份文档，全库搜就该排进前几。
#: 但 wav/mp3/mp4 用的是**同一段 TTS 音频**，转写文本字节相同 —— 搜"知识库"
#: 时三者完全等价，top_k=5 从 11 份文档里只能带回 5 个，于是 mp4 挤进去、
#: mp3/wav 没有。这是平局截断，不是检索坏了（分数 0.032786883 = 2/61，
#: 正好是 RRF 在语义和关键词两路都排第一的值）。
#:
#: 所以音视频加 document_ids 限定到这一份，把"这份文档的向量能不能被检索到"
#: 和"同义文档之间谁排前面"分开判 —— 后者是测试数据的性质，不是产品缺陷。
AMBIGUOUS = {"wav", "mp3", "mp4"}

for ext, doc_id, phrase in searchable:
    # 字段是 kb_ids（list[int]），不是 kb_id。
    #
    # 第一版发的是 kb_id —— Pydantic 直接忽略未知字段，于是 effective_kb_ids
    # 为 None，退回按 user_id="" 查可见库，8 个格式全返回 0 条。看起来像
    # "检索整个坏了"，实际是我把参数名写错了。
    body = {
        "query": phrase,
        "kb_ids": [kb_id],
        "top_k": 5,
        "tenant_id": TENANT,
    }
    scope = "全库"
    if ext in AMBIGUOUS:
        body["document_ids"] = [doc_id]
        scope = "限定本文档"

    st, res = call("POST", "/kb/search", body, timeout=180)
    if st != 200:
        bad(f"[{ext}] 搜「{phrase}」HTTP {st}: {str(res)[:150]}")
        continue
    # 响应是 list[SearchHitOut]，不是 {"results": [...]}。
    items = res if isinstance(res, list) else (res or {}).get("results")
    if not items:
        bad(f"[{ext}] 搜「{phrase}」({scope}) 返回 0 条 —— 内容在库里但搜不到")
        continue
    # document_id 在响应里是 str（SearchHitOut 声明成 str），库里是 int
    ids = [str(it.get("document_id")) for it in items]
    if str(doc_id) in ids:
        top = items[0]
        good(f"[{ext}] 搜「{phrase}」({scope}) 命中 doc={doc_id}"
             f"（{len(items)} 条，首条 score={top.get('score')}）")
    else:
        bad(f"[{ext}] 搜「{phrase}」({scope}) 命中的是 {ids}，没有 doc={doc_id}")

# ── 汇总 ────────────────────────────────────────────────────────────────────
print()
print("=" * 74)
print("格式矩阵汇总")
print("=" * 74)
print(f"  {'格式':<6} {'预期':<6} {'实际':<6} {'HTTP':<6}")
for ext, exp, got, st in results:
    mark = "ok" if exp == got else "★不符"
    print(f"  {ext:<6} {'收' if exp else '拒':<6} {'收' if got else '拒':<6} {st:<6} {mark}")

print()
for n in notes:
    print(f"  ⚠ {n}")
print()
print("=" * 74)
print("PASS" if ok else "FAIL")
print("=" * 74)
st, _ = call("DELETE", f"/knowledge-bases/{kb_id}")
print(f"清理测试库 id={kb_id}: {st}（401 = 要用户身份，内部 token 不行）")
sys.exit(0 if ok else 1)
