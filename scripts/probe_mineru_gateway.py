"""探 202.205.160.8:8002 的 MinerU Secure Gateway：鉴权能不能过、支持哪些格式。

为什么要探：parser.py 里已经有本地 MinerU 的分支（_parse_pdf_mineru_local 调
POST {base_url}/file_parse），但线上 MINERU_URL 没设，走的是 mineru.net 云端。
而官方文档说 Office 格式**只走本地原生解析**，云端不支持 —— 所以如果这个网关
认我们的凭证、而且支持 docx/pptx/xlsx，就能一次解决三件事：
去掉云端外部依赖、Office 不再绕道 python-docx/python-pptx、多出 XLSX 等格式。

token 从环境变量读，不进命令行（命令行会落到 ps 和 history）。

跑法（在 rag 容器里，它和 160.8 之间的连通性已验证）：
    docker cp scripts/probe_mineru_gateway.py lea-rag-test:/tmp/p.py
    docker exec -e MINERU_GW_TOKEN=... lea-rag-test python /tmp/p.py
"""

from __future__ import annotations

import io
import json
import os
import struct
import sys
import urllib.error
import urllib.request
import uuid
import zlib

BASE = os.getenv("MINERU_GW_URL", "http://202.205.160.8:8002")
TOKEN = os.getenv("MINERU_GW_TOKEN", "")


def _auth() -> dict:
    return {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}


def get(path: str) -> tuple[int, str]:
    try:
        req = urllib.request.Request(f"{BASE}{path}", headers=_auth())
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace")[:400]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as exc:
        return -1, f"{type(exc).__name__}: {exc}"


def post_file(path: str, filename: str, content: bytes, mime: str,
              fields: dict | None = None) -> tuple[int, str]:
    """multipart 上传。字段名用 files —— 和 _parse_pdf_mineru_local 一致。"""
    boundary = f"----probe{uuid.uuid4().hex[:12]}"
    body = io.BytesIO()
    for k, v in (fields or {}).items():
        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
        body.write(f"{v}\r\n".encode())
    body.write(f"--{boundary}\r\n".encode())
    body.write(
        f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'.encode()
    )
    body.write(f"Content-Type: {mime}\r\n\r\n".encode())
    body.write(content)
    body.write(f"\r\n--{boundary}--\r\n".encode())

    hdr = _auth()
    hdr["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    req = urllib.request.Request(f"{BASE}{path}", data=body.getvalue(),
                                 method="POST", headers=hdr)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:400]
    except Exception as exc:
        return -1, f"{type(exc).__name__}: {exc}"


def make_png() -> bytes:
    w = h = 16
    raw = b"".join(b"\x00" + b"\x00\x00\xff" * w for _ in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def make_docx() -> bytes:
    from docx import Document as Docx

    d = Docx()
    d.add_heading("探测用标题", level=1)
    d.add_paragraph("这是正文第一段，用来确认 MinerU 能不能原生解析 docx。")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "季度"
    t.cell(0, 1).text = "营收"
    t.cell(1, 0).text = "Q3"
    t.cell(1, 1).text = "1200万"
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def make_xlsx() -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["季度", "营收"])
    ws.append(["Q3", 1200])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def make_minimal_pdf() -> bytes:
    """手搓最小 PDF，不依赖库。"""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length 54 >>\nstream\nBT /F1 14 Tf 20 100 Td (MinerU probe page) Tj ET\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n"
            f"{xref}\n%%EOF\n").encode()
    return bytes(out)


def brief(raw: str) -> str:
    """只打结构，不打全文 —— 解析结果可能很长。"""
    try:
        d = json.loads(raw)
    except Exception:
        return raw[:200].replace("\n", " ")
    if isinstance(d, dict):
        keys = list(d.keys())
        out = f"keys={keys}"
        res = d.get("results")
        if isinstance(res, dict) and res:
            k = list(res.keys())[0]
            inner = res[k]
            if isinstance(inner, dict):
                md = str(inner.get("md_content", ""))
                out += f" | results[{k}].keys={list(inner.keys())}"
                if md:
                    out += f" | md前80={md[:80]!r}"
        return out
    return str(d)[:200]


def main() -> int:
    print(f"BASE  = {BASE}")
    print(f"token = {'已提供 len=' + str(len(TOKEN)) if TOKEN else '**未提供**'}")

    print("\n=== 1. 鉴权 ===")
    for p in ("/health", "/openapi.json"):
        code, body = get(p)
        print(f"  GET {p:16s} HTTP {code}  {body[:120].replace(chr(10), ' ')}")

    print("\n=== 2. /file_parse 各格式 ===")
    cases = [
        ("PDF", "probe.pdf", make_minimal_pdf, "application/pdf"),
        ("PNG", "probe.png", make_png, "image/png"),
        ("DOCX", "probe.docx", make_docx,
         "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("XLSX", "probe.xlsx", make_xlsx,
         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ]
    results = {}
    for label, fname, maker, mime in cases:
        try:
            content = maker()
        except Exception as exc:
            print(f"  {label:5s} 造文件失败: {type(exc).__name__}: {exc}")
            continue
        code, body = post_file(
            "/file_parse", fname, content, mime,
            {"lang_list": '["ch"]', "return_md": "true",
             "return_content_list": "true", "return_images": "true"},
        )
        results[label] = code
        print(f"  {label:5s} ({len(content):6d}B) HTTP {code}  {brief(body)[:260]}")

    print("\n=== 小结 ===")
    for label, code in results.items():
        verdict = "支持" if code == 200 else ("鉴权失败" if code == 401 else f"不支持/异常({code})")
        print(f"  {label:5s} {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
