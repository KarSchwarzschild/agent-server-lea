"""PDF 解析服务 — MinerU 云端 API (mineru.net) 优先，PyMuPDF fallback。"""

from __future__ import annotations

import io
import json
import os
import re
import time
import zipfile

import httpx
import structlog

logger = structlog.get_logger()

_MINERU_API_BASE = "https://mineru.net/api/v4"
_MINERU_TOKEN: str | None = None


def _get_mineru_token() -> str:
    global _MINERU_TOKEN
    if _MINERU_TOKEN is None:
        _MINERU_TOKEN = os.getenv("MINERU_TOKEN", "")
    return _MINERU_TOKEN


def _html_table_to_text(html: str) -> str:
    html = re.sub(r"<tr>", "\n", html)
    html = re.sub(r"</?(table|tbody|thead|tfoot)[^>]*>", "", html)
    html = re.sub(r"<(td|th)[^>]*>", " | ", html)
    html = re.sub(r"</(td|th|tr)>", "", html)
    html = re.sub(r"<[^>]+>", "", html)
    lines = [line.strip().strip("|").strip() for line in html.split("\n")]
    return "\n".join(line for line in lines if line)


def _parse_content_list(content_list_raw, images_dict: dict) -> dict:
    items = json.loads(content_list_raw) if isinstance(content_list_raw, str) else content_list_raw
    text_blocks: list[dict] = []
    image_items: list[dict] = []

    for item in items:
        item_type = item.get("type", "")
        page_idx = item.get("page_idx", 0)

        if item_type == "text":
            text = item.get("text", "").strip()
            if text:
                text_blocks.append({"text": text, "page_idx": page_idx})

        elif item_type == "list":
            list_items = item.get("list_items", [])
            text = "\n".join(list_items).strip() or item.get("text", "").strip()
            if text:
                text_blocks.append({"text": text, "page_idx": page_idx})

        elif item_type == "table":
            table_body = item.get("table_body", "")
            caption_list = item.get("table_caption", [])
            caption = caption_list[0] if caption_list else ""
            table_text = _html_table_to_text(table_body)
            if caption:
                table_text = f"表格：{caption}\n{table_text}"
            if table_text.strip():
                text_blocks.append({"text": table_text, "page_idx": page_idx})

        elif item_type == "image":
            img_path = item.get("img_path", "")
            img_filename = img_path.split("/")[-1] if "/" in img_path else img_path
            base64_data = images_dict.get(img_filename, "")
            caption_list = item.get("image_caption", [])
            caption = caption_list[0] if caption_list else ""
            if base64_data:
                image_items.append({
                    "img_path": img_path,
                    "base64": base64_data,
                    "page_idx": page_idx,
                    "caption": caption,
                })

    return {"text_blocks": text_blocks, "image_items": image_items}


def _split_into_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？])", text)
    sentences: list[str] = []
    for part in parts:
        stripped = part.strip()
        if not stripped:
            continue
        if stripped in "。！？" and sentences:
            sentences[-1] += stripped
        else:
            sentences.append(stripped)
    return sentences


def _join_text_blocks(texts: list[str]) -> str:
    if not texts:
        return ""
    result = texts[0]
    for t in texts[1:]:
        if t and t[0] in "。！？；，、）」》】：":
            result += t
        else:
            result += "\n" + t
    return result


def chunk_text_blocks(text_blocks: list[dict], chunk_size: int = 200) -> list[dict]:
    """将 text_blocks 按句子边界分块，附加 Sentence Window 上下文。"""
    if not text_blocks:
        return []

    pages: dict[int, list[str]] = {}
    for block in text_blocks:
        pid = block["page_idx"]
        pages.setdefault(pid, []).append(block["text"])

    all_small: list[dict] = []
    for page_idx in sorted(pages.keys()):
        page_text = _join_text_blocks(pages[page_idx])
        sentences = _split_into_sentences(page_text)
        current = ""
        for sentence in sentences:
            if len(sentence) > chunk_size:
                if current:
                    all_small.append({"content": current, "page_idx": page_idx})
                    current = ""
                all_small.append({"content": sentence, "page_idx": page_idx})
                continue
            candidate = (current + "\n" + sentence).strip() if current else sentence
            if len(candidate) <= chunk_size:
                current = candidate
            else:
                if current:
                    all_small.append({"content": current, "page_idx": page_idx})
                current = sentence
        if current.strip():
            all_small.append({"content": current, "page_idx": page_idx})

    all_small = [c for c in all_small if len(c["content"].strip()) >= 2]

    window_size = 1
    chunks: list[dict] = []
    for i, chunk in enumerate(all_small):
        start = max(0, i - window_size)
        end = min(len(all_small), i + window_size + 1)
        window = "\n\n".join(all_small[j]["content"] for j in range(start, end))
        chunks.append({
            "content": chunk["content"],
            "window_content": window,
            "page": chunk["page_idx"] + 1,
            "type": "text",
        })
    return chunks


def _poll_batch_done(batch_id: str, token: str, timeout: int = 300) -> str:
    """轮询 batch 状态直到完成，返回第一个文件的 task_id。"""
    headers = {"Authorization": f"Bearer {token}"}
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = httpx.get(
            f"{_MINERU_API_BASE}/extract-results/batch/{batch_id}",
            headers=headers, timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"MinerU poll error: {data}")
        extract_data = data.get("data", {})
        results = extract_data.get("extract_result", [])
        if not results:
            # v4 API 可能用 file_results 字段
            results = extract_data.get("file_results", [])
        logger.info(
            "mineru_poll",
            batch_id=batch_id,
            keys=list(extract_data.keys()),
            results_len=len(results),
            first=results[0] if results else None,
        )
        if results and results[0].get("state") == "done":
            # v4 API 直接返回 full_zip_url，不需要二次请求
            zip_url = results[0].get("full_zip_url")
            if zip_url:
                return zip_url
            task_id = results[0].get("task_id") or results[0].get("id") or results[0].get("file_id") or batch_id
            return task_id
        if results and results[0].get("state") == "failed":
            raise RuntimeError(f"MinerU 解析失败: {results[0]}")
        time.sleep(3)
    raise RuntimeError(f"MinerU 解析超时 ({timeout}s)")


def _download_and_parse_zip(task_id_or_url: str, token: str) -> dict:
    """下载解析结果 zip，提取 md / content_list / images。"""
    if task_id_or_url.startswith("http"):
        zip_url = task_id_or_url
    else:
        headers = {"Authorization": f"Bearer {token}"}
        resp = httpx.get(
            f"{_MINERU_API_BASE}/extract/task/{task_id_or_url}",
            headers=headers, timeout=30,
        )
        resp.raise_for_status()
        task_data = resp.json()["data"]
        zip_url = task_data.get("full_zip_url", "")
        if not zip_url:
            md_content = task_data.get("md_content", "")
            return {"md_content": md_content, "text_chunks": _chunk_md_fallback(md_content), "image_items": []}

    zip_resp = httpx.get(zip_url, timeout=120)
    zip_resp.raise_for_status()

    md_content = ""
    content_list_raw = None
    images_dict: dict[str, str] = {}

    with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as zf:
        for name in zf.namelist():
            if name.endswith(".md") and not md_content:
                md_content = zf.read(name).decode("utf-8", errors="ignore")
            elif name.endswith("content_list.json") and "_v2" not in name:
                content_list_raw = json.loads(zf.read(name))
            elif "images/" in name and not name.endswith("/"):
                import base64
                img_name = name.split("/")[-1]
                images_dict[img_name] = base64.b64encode(zf.read(name)).decode()

    if content_list_raw:
        parsed = _parse_content_list(content_list_raw, images_dict)
        text_chunks = chunk_text_blocks(parsed["text_blocks"])
        return {"md_content": md_content, "text_chunks": text_chunks, "image_items": parsed["image_items"]}

    text_chunks = _chunk_md_fallback(md_content)
    return {"md_content": md_content, "text_chunks": text_chunks, "image_items": []}


def parse_pdf_mineru(file_path: str) -> dict:
    """MinerU 解析入口 — 自动选择本地或云端模式。"""
    local_url = os.getenv("MINERU_URL", "")
    if local_url:
        return _parse_pdf_mineru_local(file_path, local_url)
    return _parse_pdf_mineru_cloud(file_path)


def _parse_pdf_mineru_local(file_path: str, base_url: str) -> dict:
    """本地自部署 MinerU（同步 /file_parse 接口）。"""
    with open(file_path, "rb") as f:
        filename = os.path.basename(file_path)
        files = {"files": (filename, f, "application/pdf")}
        data = {
            "lang_list": '["ch"]',
            "return_md": "true",
            "return_content_list": "true",
            "return_images": "true",
        }
        resp = httpx.post(f"{base_url.rstrip('/')}/file_parse", files=files, data=data, timeout=300)

    if resp.status_code != 200:
        raise RuntimeError(f"MinerU 本地解析失败: {resp.text}")

    result = resp.json()
    file_key = list(result["results"].keys())[0]
    file_result = result["results"][file_key]

    md_content = file_result.get("md_content", "")
    content_list_raw = file_result.get("content_list")
    images_dict = file_result.get("images", {})

    if content_list_raw:
        parsed = _parse_content_list(content_list_raw, images_dict)
        text_chunks = chunk_text_blocks(parsed["text_blocks"])
        return {"md_content": md_content, "text_chunks": text_chunks, "image_items": parsed["image_items"]}

    text_chunks = _chunk_md_fallback(md_content)
    return {"md_content": md_content, "text_chunks": text_chunks, "image_items": []}


def _parse_pdf_mineru_cloud(file_path: str) -> dict:
    """MinerU 云端 API（异步提交 → 轮询 → 下载 zip）。"""
    token = _get_mineru_token()
    if not token:
        raise RuntimeError("MINERU_TOKEN 未配置")

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    filename = os.path.basename(file_path)

    batch_resp = httpx.post(
        f"{_MINERU_API_BASE}/file-urls/batch",
        headers=headers,
        json={"files": [{"name": filename, "is_ocr": False}], "model_version": "vlm"},
        timeout=30,
    )
    batch_resp.raise_for_status()
    batch_data = batch_resp.json()
    if batch_data.get("code") != 0:
        raise RuntimeError(f"MinerU batch create failed: {batch_data}")

    batch_id = batch_data["data"]["batch_id"]
    upload_url = batch_data["data"]["file_urls"][0]

    with open(file_path, "rb") as f:
        put_resp = httpx.put(upload_url, content=f.read(), timeout=120)
    put_resp.raise_for_status()

    task_id = _poll_batch_done(batch_id, token)
    return _download_and_parse_zip(task_id, token)


def parse_pdf_pymupdf(file_path: str) -> dict:
    """PyMuPDF fallback — 纯文本提取 + 分块。"""
    import fitz

    doc = fitz.open(file_path)
    text_blocks: list[dict] = []
    full_text_parts: list[str] = []

    for page_num, page in enumerate(doc):
        text = page.get_text()
        if text.strip():
            text_blocks.append({"text": text.strip(), "page_idx": page_num})
            full_text_parts.append(text.strip())

    doc.close()

    md_content = "\n\n".join(full_text_parts)
    text_chunks = chunk_text_blocks(text_blocks)
    return {"md_content": md_content, "text_chunks": text_chunks, "image_items": []}


def _chunk_md_fallback(md_content: str) -> list[dict]:
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=200,
        chunk_overlap=30,
        separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],
    )
    docs = splitter.split_text(md_content)
    chunks: list[dict] = []
    for i, text in enumerate(docs):
        start = max(0, i - 1)
        end = min(len(docs), i + 2)
        window = "\n\n".join(docs[start:end])
        chunks.append({"content": text, "window_content": window, "page": 0, "type": "markdown"})
    return chunks


def parse_document(file_path: str, parser: str = "mineru") -> dict:
    """统一入口。返回 {md_content, text_chunks, image_items}。"""
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""

    if ext == "docx":
        return parse_docx(file_path)
    if ext == "pptx":
        return parse_pptx(file_path)
    if ext in ("png", "jpg", "jpeg", "webp", "gif", "bmp"):
        return parse_image(file_path)
    if ext in ("mp3", "wav", "m4a", "flac", "ogg"):
        return parse_audio(file_path)

    if parser == "mineru":
        try:
            return parse_pdf_mineru(file_path)
        except Exception as exc:
            logger.warning("mineru_fallback", error=str(exc))
            return parse_pdf_pymupdf(file_path)
    return parse_pdf_pymupdf(file_path)


def parse_docx(file_path: str) -> dict:
    """DOCX 解析 — python-docx 提取段落 + 表格 + 内嵌图片。"""
    import base64

    from docx import Document as DocxDocument

    doc = DocxDocument(file_path)
    text_blocks: list[dict] = []
    image_items: list[dict] = []

    for i, para in enumerate(doc.paragraphs):
        text = para.text.strip()
        if text:
            text_blocks.append({"text": text, "page_idx": 0})

    for table in doc.tables:
        rows_text = []
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            rows_text.append(" | ".join(cells))
        table_text = "\n".join(rows_text)
        if table_text.strip():
            text_blocks.append({"text": f"表格：\n{table_text}", "page_idx": 0})

    for rel in doc.part.rels.values():
        if "image" in rel.reltype:
            try:
                img_data = rel.target_part.blob
                b64 = base64.b64encode(img_data).decode()
                image_items.append({
                    "base64": b64,
                    "page_idx": 0,
                    "caption": "",
                    "img_path": rel.target_ref,
                })
            except Exception:
                pass

    md_content = "\n\n".join(b["text"] for b in text_blocks)
    text_chunks = chunk_text_blocks(text_blocks)
    return {"md_content": md_content, "text_chunks": text_chunks, "image_items": image_items}


def parse_pptx(file_path: str) -> dict:
    """PPTX 解析 — python-pptx 逐 slide 提取文本 + 图片。"""
    import base64

    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    prs = Presentation(file_path)
    text_blocks: list[dict] = []
    image_items: list[dict] = []

    for slide_idx, slide in enumerate(prs.slides):
        slide_texts: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    text = para.text.strip()
                    if text:
                        slide_texts.append(text)
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                try:
                    img_data = shape.image.blob
                    b64 = base64.b64encode(img_data).decode()
                    image_items.append({
                        "base64": b64,
                        "page_idx": slide_idx,
                        "caption": "",
                        "img_path": f"slide_{slide_idx + 1}.png",
                    })
                except Exception:
                    pass
            if shape.has_table:
                rows_text = []
                for row in shape.table.rows:
                    cells = [cell.text.strip() for cell in row.cells]
                    rows_text.append(" | ".join(cells))
                table_text = "\n".join(rows_text)
                if table_text.strip():
                    slide_texts.append(f"表格：\n{table_text}")

        if slide_texts:
            combined = "\n".join(slide_texts)
            text_blocks.append({"text": combined, "page_idx": slide_idx})

    md_content = "\n\n".join(b["text"] for b in text_blocks)
    text_chunks = chunk_text_blocks(text_blocks)
    return {"md_content": md_content, "text_chunks": text_chunks, "image_items": image_items}


def parse_image(file_path: str) -> dict:
    """图片文件直接入库 — 不做文本提取，由 kb_processor 走 VLM/multimodal 路径。"""
    import base64

    with open(file_path, "rb") as f:
        img_data = f.read()

    b64 = base64.b64encode(img_data).decode()
    filename = os.path.basename(file_path)
    image_items = [{
        "base64": b64,
        "page_idx": 0,
        "caption": "",
        "img_path": filename,
    }]
    return {"md_content": "", "text_chunks": [], "image_items": image_items}


def parse_audio(file_path: str) -> dict:
    """音频文件解析骨架 — 需要 ASR 服务就绪后启用。

    当前行为：返回空文本 + 音频元信息，由 kb_processor 调用 ASR 转写。
    """
    filename = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)
    return {
        "md_content": "",
        "text_chunks": [],
        "image_items": [],
        "audio_items": [{
            "file_path": file_path,
            "filename": filename,
            "file_size": file_size,
        }],
    }
