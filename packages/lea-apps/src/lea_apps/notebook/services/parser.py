"""PDF 解析服务 — MinerU 优先，PyMuPDF fallback。"""

from __future__ import annotations

import json
import re

import httpx
import structlog

logger = structlog.get_logger()

_MINERU_URL = None


def _get_mineru_url() -> str:
    global _MINERU_URL
    if _MINERU_URL is None:
        from lea_server.config import get_settings
        _MINERU_URL = getattr(get_settings(), "mineru_url", "http://localhost:8001")
    return _MINERU_URL


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


def parse_pdf_mineru(file_path: str) -> dict:
    """用 MinerU 解析 PDF，返回 md_content + text_chunks + image_items。"""
    url = f"{_get_mineru_url()}/file_parse"

    with open(file_path, "rb") as f:
        filename = file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path.rsplit("\\", 1)[-1]
        files = {"files": (filename, f, "application/pdf")}
        data = {
            "lang_list": ["ch"],
            "return_md": True,
            "return_content_list": True,
            "return_images": True,
        }
        resp = httpx.post(url, files=files, data=data, timeout=300)

    if resp.status_code != 200:
        raise RuntimeError(f"MinerU 解析失败: {resp.text}")

    result = resp.json()
    file_key = list(result["results"].keys())[0]
    file_result = result["results"][file_key]

    md_content = file_result.get("md_content", "")
    content_list_raw = file_result.get("content_list")
    images_dict = file_result.get("images", {})

    if content_list_raw:
        parsed = _parse_content_list(content_list_raw, images_dict)
        text_chunks = chunk_text_blocks(parsed["text_blocks"])
        return {
            "md_content": md_content,
            "text_chunks": text_chunks,
            "image_items": parsed["image_items"],
        }

    text_chunks = _chunk_md_fallback(md_content)
    return {"md_content": md_content, "text_chunks": text_chunks, "image_items": []}


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
    if parser == "mineru":
        try:
            return parse_pdf_mineru(file_path)
        except Exception as exc:
            logger.warning("mineru_fallback", error=str(exc))
            return parse_pdf_pymupdf(file_path)
    return parse_pdf_pymupdf(file_path)
