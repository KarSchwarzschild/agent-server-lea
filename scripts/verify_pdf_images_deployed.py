#!/usr/bin/env python3
"""PDF 里的图片进不进知识库 —— MinerU 降级的实际代价。

在 lea-rag 容器里跑：
    docker exec lea-rag-test python /tmp/verify_pdf_images_deployed.py /tmp/fmt

## 背景

MINERU_TOKEN 失效（MinerU 官方 API 回 401 "user token expired"，而且这个
token 是 sk-xxx 形态不是 JWT）。于是每份 PDF 都走 parse_pdf_mineru 失败
→ 降级到 parse_pdf_pymupdf，而后者的返回里 `"image_items": []` 是写死的空。

推论：PDF 里的图表提取不出来。这个脚本实测它。

## 判定

样本是图文混排 PDF，两个短语互相区分：
    正文短语  在文字层   → 降级路径也该提取到
    图片短语  在嵌入图里 → 要走 image_items + VLM 才拿得到

    两个都命中 → PDF 图文全进得去
    只有正文命中 → 文本进得去、图片丢了（推论成立）
    都没命中 → PDF 整个坏了

顺带做一次**对照**：同一张图单独传成 .png 必须能被 VLM 描述出来。
这一步把"VLM 认不出这张图"和"PDF 没把图交出来"分开 —— 否则图片短语
没命中时，分不清是哪一环的问题。
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
TENANT = "verify-imgpdf"

ok = True


def bad(m: str) -> None:
    global ok
    ok = False
    print(f"  ✗ {m}")


def good(m: str) -> None:
    print(f"  ✓ {m}")


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


def upload(kb_id: int, path: str, as_name: str | None = None):
    name = as_name or os.path.basename(path)
    with open(path, "rb") as f:
        content = f.read()
    b = "----imgpdf0123456789"
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


def wait(kb_id: int, doc_ids: set[int], limit: int = 36) -> dict[int, dict]:
    final: dict[int, dict] = {}
    pending = set(doc_ids)
    for _ in range(limit):
        time.sleep(5)
        st, docs = call("GET", f"/kb/{kb_id}/documents")
        if st == 200 and isinstance(docs, list):
            for d in docs:
                if d.get("id") in pending and d.get("status") in ("ready", "error"):
                    final[d["id"]] = d
                    pending.discard(d["id"])
        if not pending:
            break
    return final


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


def milvus_rows(doc_id: int) -> list[dict]:
    try:
        from pymilvus import Collection, connections
        try:
            connections.connect(
                "imgpdf",
                host=os.getenv("MILVUS_HOST", "milvus"),
                port=os.getenv("MILVUS_PORT", "19530"),
            )
        except Exception:  # noqa: BLE001
            pass
        c = Collection("lea_knowledge_v2", using="imgpdf")
        c.load()
        return c.query(
            expr=f"document_id == {doc_id}",
            output_fields=["document_id", "chunk_type", "content"],
            limit=50,
        )
    except Exception as e:  # noqa: BLE001
        print(f"  （查 Milvus 失败：{type(e).__name__}: {e}）")
        return []


man = json.load(open(os.path.join(FIX, "imgpdf_manifest.json"), encoding="utf-8"))
PDF = os.path.join(FIX, man["file"])
TEXT_PHRASE = man["text_phrase"]
IMAGE_PHRASE = man["image_phrase"]

# 第二个样本：嵌「无文字图形」的 PDF。有则一起跑。
PHOTO_MAN = os.path.join(FIX, "photopdf_manifest.json")
photo_man = (
    json.load(open(PHOTO_MAN, encoding="utf-8")) if os.path.exists(PHOTO_MAN) else None
)

print("=" * 72)
print("MinerU 状态")
print("=" * 72)
from lea_rag.parsers import parser as P  # noqa: E402

tok = os.getenv("MINERU_TOKEN", "")
print(f"  MINERU_URL（本地）: {P.mineru_local_url() or '未配置 → 云端模式'}")
print(f"  MINERU_TOKEN: {len(tok)} 字符，JWT={len(tok.split('.')) == 3}")
print(f"  pdf 进 MinerU 吗: {'pdf' in P.mineru_native_exts()}")
print()
print(f"  样本 A {man['file']}：文字层含「{TEXT_PHRASE}」，"
      f"嵌入 {man['embedded_images']} 张图（图里写着「{IMAGE_PHRASE}」）")
if photo_man:
    print(f"  样本 B {photo_man['file']}：文字层含「{photo_man['text_phrase']}」，"
          f"嵌入 {photo_man['embedded_images']} 张**无文字**图形")
print()
print("  两个样本分辨两条路：")
print("    A 的图里有字 → MinerU 可能 OCR 进正文，不一定走图片路径")
print("    B 的图没有字 → OCR 无从下手，只有图片路径才能让它进库")
print()

st, kb = call("POST", "/kb/create", {
    "name": f"imgpdf-{int(time.time())}",
    "tenant_id": TENANT,
    "description": "PDF 图片提取验证，可删",
})
if st not in (200, 201) or not isinstance(kb, dict):
    print(f"建库失败 {st}: {kb}")
    sys.exit(1)
kb_id = kb["id"]
print(f"测试库 id={kb_id}")
print()

# ── 1. 传图文 PDF ───────────────────────────────────────────────────────────
print("=" * 72)
print("1. 图文混排 PDF")
print("=" * 72)
st, resp = upload(kb_id, PDF)
if st not in (200, 201, 202) or not isinstance(resp, dict):
    bad(f"上传失败 {st}: {resp}")
    sys.exit(1)
pdf_doc = resp["id"]
good(f"上传成功 doc={pdf_doc}")

# ── 2. 对照：同一张图单独传 png ────────────────────────────────────────────
# 从 PDF 里把图抠出来，保证和嵌入的是同一张
import fitz  # noqa: E402

d = fitz.open(PDF)
imgs = d[0].get_images(full=True)
png_doc = None
if imgs:
    xref = imgs[0][0]
    pix = fitz.Pixmap(d, xref)
    png_path = "/tmp/_imgpdf_extracted.png"
    pix.save(png_path)
    d.close()
    print()
    print("=" * 72)
    print("2. 对照组：同一张图单独传 .png")
    print("=" * 72)
    st, resp2 = upload(kb_id, png_path, "control.png")
    if st in (200, 201, 202) and isinstance(resp2, dict):
        png_doc = resp2["id"]
        good(f"上传成功 doc={png_doc}")
    else:
        bad(f"对照组上传失败 {st}: {resp2}")
    os.unlink(png_path)
else:
    d.close()
    bad("样本里没有嵌入图片")

# ── 等解析 ──────────────────────────────────────────────────────────────────
print()
print("  等解析落地…")
targets = {x for x in (pdf_doc, png_doc) if x}
final = wait(kb_id, targets)

# ── 3. 判定 ─────────────────────────────────────────────────────────────────
print()
print("=" * 72)
print("3. PDF 的文本和图片各自进去了吗")
print("=" * 72)

d = final.get(pdf_doc)
if not d:
    bad("PDF 没拿到终态")
else:
    print(f"  状态={d.get('status')} chunks={d.get('chunk_count')} "
          f"错误={(d.get('error_message') or '')[:120]}")
    body = chunks_of(pdf_doc)
    rows = milvus_rows(pdf_doc)
    types = {}
    for r in rows:
        types[r.get("chunk_type")] = types.get(r.get("chunk_type"), 0) + 1
    print(f"  正文 {len(body)} 字符，Milvus {len(rows)} 行 {types}")
    print()

    if TEXT_PHRASE in body:
        good(f"文字层提取到「{TEXT_PHRASE}」—— PDF 文本进得去")
    else:
        bad(f"文字层没提到「{TEXT_PHRASE}」，正文：{body[:150]!r}")

    caps = [r.get("content") or "" for r in rows if r.get("chunk_type") == "image_caption"]
    # 分两条路报，别混成一句"图片进得去"。
    #
    # 实测（换上有效 token 后）：图里的 CRIMSONKEEL-5518 出现在 **text chunk**
    # 里，Milvus 只有 {'text': 1}，0 条 image_caption。说明 MinerU 把这张图判成
    # 文字区域、OCR 出来拼进了正文 —— 对这个样本有效，但不能由此说"图片路径通"，
    # 真实图表 OCR 不出东西。所以才有样本 B。
    if caps and IMAGE_PHRASE in "\n".join(caps):
        good(f"图片路径：{len(caps)} 条 image_caption，含「{IMAGE_PHRASE}」")
    elif IMAGE_PHRASE in body:
        good(f"OCR 路径：图里的「{IMAGE_PHRASE}」被 OCR 进了正文"
             f"（Milvus {types}，无 image_caption）")
        print("    → 这张图有文字所以能 OCR。真实图表走不了这条路，见样本 B")
    elif caps:
        bad(f"有 {len(caps)} 条 image_caption 但不含「{IMAGE_PHRASE}」："
            f"{caps[0][:120]!r}")
    else:
        bad("图里的短语既不在正文也没有 image_caption —— 图片内容整个丢了")

# ── 4. 对照判定 ─────────────────────────────────────────────────────────────
print()
print("=" * 72)
print("4. 对照组：这张图单独传能不能被认出来")
print("=" * 72)
if png_doc:
    rows = milvus_rows(png_doc)
    caps = [r.get("content") or "" for r in rows if r.get("chunk_type") == "image_caption"]
    if not caps:
        bad("对照组也没有 image_caption —— 图片链路整个有问题，不只是 PDF")
    elif IMAGE_PHRASE in "\n".join(caps):
        good(f"单独传的 png 认出了「{IMAGE_PHRASE}」"
             f"（描述 {len(caps[0])} 字符）")
        print("    → 所以上面 PDF 没拿到图片短语，不是 VLM 认不出，"
              "是 PDF 解析没把图交出来")
    else:
        print(f"  ⚠ 单独传的 png 有描述但不含「{IMAGE_PHRASE}」：{caps[0][:120]!r}")
        print("    → VLM 这次没认出图中文字，所以 PDF 那一项无法归因，"
              "需要换个更清晰的图重测")

# ── 5. 样本 B：无文字图形，这才是真图表的代理 ──────────────────────────────
photo_verdict = None
if photo_man:
    print()
    print("=" * 72)
    print("5. 样本 B：嵌「无文字图形」的 PDF（OCR 无从下手）")
    print("=" * 72)
    ppdf = os.path.join(FIX, photo_man["file"])
    st, resp3 = upload(kb_id, ppdf)
    if st not in (200, 201, 202) or not isinstance(resp3, dict):
        bad(f"样本 B 上传失败 {st}: {resp3}")
    else:
        pdoc = resp3["id"]
        good(f"上传成功 doc={pdoc}")
        print("  等解析落地…")
        f2 = wait(kb_id, {pdoc})
        d2 = f2.get(pdoc)
        if not d2:
            bad("样本 B 没拿到终态")
        else:
            pbody = chunks_of(pdoc)
            prows = milvus_rows(pdoc)
            ptypes: dict = {}
            for r in prows:
                ptypes[r.get("chunk_type")] = ptypes.get(r.get("chunk_type"), 0) + 1
            pcaps = [
                r.get("content") or "" for r in prows
                if r.get("chunk_type") in ("image_caption", "image")
            ]
            print(f"  状态={d2.get('status')} chunks={d2.get('chunk_count')}，"
                  f"Milvus {len(prows)} 行 {ptypes}")

            tp = photo_man["text_phrase"]
            if tp in pbody:
                good(f"文字层提取到「{tp}」")
            else:
                bad(f"文字层没提到「{tp}」：{pbody[:120]!r}")

            kws = photo_man["image_keywords"]
            capjoin = "\n".join(pcaps)
            hit = [k for k in kws if k in capjoin]
            if pcaps and hit:
                good(f"图片路径通：{len(pcaps)} 条图片 chunk，描述命中 {hit}")
                print(f"    描述：{capjoin[:160]!r}")
                photo_verdict = "图片路径通"
            elif pcaps:
                print(f"  ⚠ 有 {len(pcaps)} 条图片 chunk 但描述没命中 {kws}：")
                print(f"    {capjoin[:200]!r}")
                print("    → 图片交出来了，是 VLM 描述用词不同；链路通")
                photo_verdict = "图片路径通（描述用词不同）"
            else:
                print("  ✗ 0 条图片 chunk —— 无文字图形**没进知识库**")
                print("    PDF 里的真实图表（饼图、照片、示意图）检索不到")
                photo_verdict = "图片路径不通"
                ok = False

print()
print("=" * 72)
if ok:
    print("PASS —— PDF 文本和图片都进得去")
else:
    print("FAIL —— 见上面标 ✗ 的项")
if photo_verdict:
    print(f"       无文字图形：{photo_verdict}")
print("=" * 72)
st, _ = call("DELETE", f"/knowledge-bases/{kb_id}")
print(f"清理测试库 id={kb_id}: {st}")
sys.exit(0 if ok else 1)
