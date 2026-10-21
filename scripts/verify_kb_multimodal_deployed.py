"""在部署的服务上实测知识库多模态：图片 / 音频 / 视频到底能不能入库。

为什么要真上传：读代码只能证明"分支在"，证明不了链路通。这个 session 里已经
撞过好几次同一个形状 —— 10 个 record_* 全是死埋点、告警喂了非 _bucket 永远 0
序列、healthcheck 探静态文件所以 502 期间一路绿。

所以这里每一步都要有**可观测的证据**：Milvus 里的行数和 chunk_type、
容器日志里的那行 warning、API 的实际状态码。

做法：建一个临时 KB（开 vlm_enabled），上传真实文件，等 celery 处理完，
查 Milvus 有没有出对应的 chunk。跑完删掉 KB 和它的向量。

跑法：
    docker cp scripts/verify_kb_multimodal_deployed.py lea-rag-test:/tmp/mm.py
    docker exec lea-rag-test python /tmp/mm.py
"""

from __future__ import annotations

import io
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib

sys.path.insert(0, "/app/src")

BASE = "http://127.0.0.1:8300"
TENANT = "verify-mm"


def _headers(extra: dict | None = None) -> dict:
    h = {"X-Internal-Token": os.getenv("LEA_INTERNAL_TOKEN", "")}
    if extra:
        h.update(extra)
    return h


def call(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    hdr = _headers({"Content-Type": "application/json"}) if body is not None else _headers()
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method, headers=hdr)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def upload(kb_id: int, filename: str, content: bytes, mime: str):
    """multipart 手搓 —— 容器里没有 requests。"""
    boundary = "----verifymm0123456789"
    body = io.BytesIO()
    body.write(f"--{boundary}\r\n".encode())
    body.write(
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode()
    )
    body.write(f"Content-Type: {mime}\r\n\r\n".encode())
    body.write(content)
    body.write(f"\r\n--{boundary}\r\n".encode())
    body.write(b'Content-Disposition: form-data; name="tenant_id"\r\n\r\n')
    body.write(TENANT.encode())
    body.write(f"\r\n--{boundary}--\r\n".encode())
    payload = body.getvalue()

    req = urllib.request.Request(
        f"{BASE}/kb/{kb_id}/upload",
        data=payload,
        method="POST",
        headers=_headers({"Content-Type": f"multipart/form-data; boundary={boundary}"}),
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]


def make_png() -> bytes:
    """造一张 16x16 的红色 PNG（不依赖 Pillow）。"""
    w = h = 16
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def make_docx_with_image(png: bytes) -> bytes:
    """造一个同时含正文和图片的 docx。

    这是唯一能验上下文窗口的路径：纯 PNG 上传没有正文，拼不出上下文。而正文里
    刻意放一个**描述里不会出现的词**（营收），用来判断正文是否真的进了图片 chunk。
    """
    import tempfile

    from docx import Document as Docx
    from docx.shared import Inches

    d = Docx()
    d.add_paragraph("下图为本年度第三季度营收情况，同比增长百分之十二。")
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(png)
        img_path = f.name
    try:
        d.add_picture(img_path, width=Inches(1))
    finally:
        os.unlink(img_path)
    d.add_paragraph("以上数据来自财务部门的季度报表汇总。")

    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def chunk_contents(kb_id: int, chunk_type: str) -> list[str]:
    """取某种 chunk_type 的 content，用来检查上下文有没有拼进去。"""
    from lea_rag.index.milvus_index import MilvusKBIndex

    idx = MilvusKBIndex()
    rows = idx.client.query(
        collection_name=idx.collection_name,
        filter=f'kb_id == {kb_id} and chunk_type == "{chunk_type}"',
        output_fields=["content", "page", "section_id"],
        limit=20,
    )
    return rows


def make_wav() -> bytes:
    """造 0.1 秒静音 WAV（44 字节头 + 数据）。"""
    sr, n = 8000, 800
    data = b"\x00\x00" * n
    return (
        b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, sr, sr * 2, 2, 16)
        + b"data" + struct.pack("<I", len(data)) + data
    )


def milvus_counts(kb_id: int) -> dict[str, int]:
    from lea_rag.index.milvus_index import MilvusKBIndex

    idx = MilvusKBIndex()
    out = {}
    total = idx.client.query(
        collection_name=idx.collection_name,
        filter=f"kb_id == {kb_id}",
        output_fields=["count(*)"],
    )[0]["count(*)"]
    out["_total"] = total
    for t in ("text", "image", "image_caption", "audio_transcript"):
        n = idx.client.query(
            collection_name=idx.collection_name,
            filter=f'kb_id == {kb_id} and chunk_type == "{t}"',
            output_fields=["count(*)"],
        )[0]["count(*)"]
        out[t] = n
    return out


def wait_ready(kb_id: int, doc_id: int, timeout: int = 240) -> str:
    """等 celery 把文档处理完。返回最终 status。

    /kb/{id}/documents 返回的是**列表**（response_model=list[DocumentOut]），
    不是 {items: [...]}。
    """
    deadline = time.time() + timeout
    last = "?"
    while time.time() < deadline:
        st, docs = call("GET", f"/kb/{kb_id}/documents")
        if st == 200 and isinstance(docs, list):
            for d in docs:
                if d.get("id") == doc_id:
                    last = d.get("status", "?")
                    # 失败态在这个库里叫 error（update_document_status 写的），
                    # 不是 failed
                    if last in ("ready", "error", "failed"):
                        return last
        time.sleep(5)
    return f"timeout(last={last})"


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    print("=== 建临时 KB（vlm_enabled=1, multimodal=0，和线上 4 个 KB 同配置）===")
    st, kb = call("POST", "/kb/create", {
        "name": f"多模态验证_{os.getpid()}",
        "description": "verify only",
        "tenant_id": TENANT,
        "owner": "verify-bot",
        "vlm_enabled": True,
        "multimodal": False,
    })
    if st not in (200, 201):
        print(f"建 KB 失败 {st}: {kb}")
        return 1
    kb_id = kb["id"]
    print(f"  kb_id={kb_id}")

    try:
        # ── 1. 图片 ─────────────────────────────────────────────────────────
        print("\n=== 1. 上传 PNG ===")
        st, doc = upload(kb_id, "verify_red.png", make_png(), "image/png")
        print(f"  HTTP {st}  {str(doc)[:120]}")
        checks.append(("图片上传被接受", st in (200, 201), f"HTTP {st}"))
        if st in (200, 201) and isinstance(doc, dict):
            final = wait_ready(kb_id, doc["id"])
            print(f"  处理状态: {final}")
            cnt = milvus_counts(kb_id)
            print(f"  Milvus: {cnt}")
            checks.append(("图片处理完成", final == "ready", final))
            checks.append((
                "图片产生了 image_caption chunk（VLM 描述入库）",
                cnt.get("image_caption", 0) > 0,
                f"image_caption={cnt.get('image_caption')} image={cnt.get('image')}",
            ))

        # ── 1b. docx（含图 + 正文）—— 验上下文窗口 ───────────────────────
        print("\n=== 1b. 上传含图 docx（验上下文窗口）===")
        st, doc = upload(
            kb_id, "verify_report.docx", make_docx_with_image(make_png()),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        print(f"  HTTP {st}  {str(doc)[:120]}")
        if st in (200, 201) and isinstance(doc, dict):
            final = wait_ready(kb_id, doc["id"])
            print(f"  处理状态: {final}")
            rows = chunk_contents(kb_id, "image_caption")
            for r in rows:
                print(f"  image_caption: page={r.get('page')} section={r.get('section_id')}")
                print(f"    {str(r.get('content'))[:200]}")
            has_ctx = any("营收" in str(r.get("content", "")) for r in rows)
            checks.append((
                "**上下文窗口生效**：图片 chunk 里含正文的词（营收）",
                has_ctx,
                f"{len(rows)} 个 image_caption chunk",
            ))

        # ── 2. 音频 ─────────────────────────────────────────────────────────
        print("\n=== 2. 上传 WAV ===")
        before = milvus_counts(kb_id)
        st, doc = upload(kb_id, "verify_silence.wav", make_wav(), "audio/wav")
        print(f"  HTTP {st}  {str(doc)[:120]}")
        checks.append(("音频上传被接受", st in (200, 201), f"HTTP {st}"))
        if st in (200, 201) and isinstance(doc, dict):
            final = wait_ready(kb_id, doc["id"])
            after = milvus_counts(kb_id)
            print(f"  处理状态: {final}")
            print(f"  Milvus: {after}")
            # 预期：ASR 未配置 → 没有 audio_transcript chunk
            checks.append((
                "音频：ASR 未配置时不产生 transcript（预期行为）",
                after.get("audio_transcript", 0) == before.get("audio_transcript", 0),
                f"audio_transcript={after.get('audio_transcript')}",
            ))

        # ── 3. 视频 ─────────────────────────────────────────────────────────
        print("\n=== 3. 上传 MP4（预期被白名单拒绝）===")
        st, resp = upload(kb_id, "verify.mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64, "video/mp4")
        print(f"  HTTP {st}  {str(resp)[:150]}")
        checks.append((
            "视频被 API 白名单拒绝（400），不是静默误解析",
            st == 400,
            f"HTTP {st}",
        ))

        # ── 判定 ───────────────────────────────────────────────────────────
        print("\n=== 判定 ===")
        ok = True
        for name, passed, detail in checks:
            print(f"  [{'OK' if passed else 'FAIL'}] {name}  ({detail})")
            ok = ok and passed
        return 0 if ok else 1
    finally:
        print("\n=== 清理 ===")
        # 先删文档（有端点），再清 Milvus。**没有删 KB 的端点** —— server_kb.py
        # 里只有 DELETE /kb/{id}/documents/{doc_id}，所以临时 KB 会留在库里，
        # 结尾打出 id 供人工清理。
        st, docs = call("GET", f"/kb/{kb_id}/documents")
        if st == 200 and isinstance(docs, list):
            for d in docs:
                call("DELETE", f"/kb/{kb_id}/documents/{d['id']}")
            print(f"  删了 {len(docs)} 个文档")
        try:
            from lea_rag.index.milvus_index import MilvusKBIndex

            idx = MilvusKBIndex()
            idx.delete_by_kb(kb_id)
            idx.client.flush(collection_name=idx.collection_name)
            print(f"  Milvus 剩余 {milvus_counts(kb_id)['_total']} 行")
        except Exception as exc:
            print(f"  Milvus 清理失败: {exc}")
        print(f"  ⚠️ 临时 KB id={kb_id} 需要手工删（没有删 KB 的端点）")


if __name__ == "__main__":
    sys.exit(main())
