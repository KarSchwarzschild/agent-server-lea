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

# MinerU **本地**原生解析支持的格式（docs: opendatalab.github.io/MinerU）。
#
# ⚠️ 云端不支持这里的大部分。官方文档写明：PDF 和图片在四个档位（Flash/Basic/
# Standard/Advanced）都可用，而 Office、OpenDocument、EPUB、OFD、HTML/MHTML、
# CSV/TSV **只用本地 Flash 原生解析**。所以这张表只在 MINERU_URL 已配置（自部署）
# 时才生效，云端模式下仍按扩展名走各自的本地库。
#
# 不含图片：MinerU 对图片做的是 OCR（抽文字），而 parse_image 返回的是整图
# base64，交给 kb_processor 走 VLM 描述或 multimodal 向量化。对知识库场景后者
# 更有用（一张示意图 OCR 出来往往是空的）。要不要 OCR 取决于 KB 的 vlm_enabled /
# multimodal 配置，而 parse_document 这一层拿不到 kb 对象，所以不在这里决定。
#
# 不含音频：MinerU 不做 ASR。音频走 parse_audio + kb_processor._process_audio。
_MINERU_NATIVE_EXTS = frozenset({
    "pdf",
    "doc", "docx",
    "ppt", "pptx",
    "xls", "xlsx",
    "rtf",
    "odt", "ods", "odp",
    "epub",
    "ofd",
    "html", "mhtml",
    "csv", "tsv",
})

# 扩展名 → Content-Type。原来 _parse_pdf_mineru_local 把 mime 写死成
# application/pdf，传 docx 进去会带错的 content-type，服务端按 mime 分派的话会
# 失配 —— 而失配的表现是"解析出空内容"，不报错。
_EXT_MIME = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "rtf": "application/rtf",
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "odp": "application/vnd.oasis.opendocument.presentation",
    "epub": "application/epub+zip",
    "ofd": "application/ofd",
    "html": "text/html",
    "mhtml": "message/rfc822",
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
}


def mineru_local_url() -> str:
    """本地 MinerU 的地址；没配返回空串（表示走云端）。"""
    return os.getenv("MINERU_URL", "").strip()


def mineru_native_exts() -> frozenset[str]:
    """当前部署下能交给 MinerU 的扩展名。

    云端模式返回只含 pdf 的集合 —— 其余格式云端不支持（见 _MINERU_NATIVE_EXTS
    的说明），交过去只会失败或拿到空内容。
    """
    return _MINERU_NATIVE_EXTS if mineru_local_url() else frozenset({"pdf"})


#: 图片：返回整图交给 VLM / multimodal，不做 OCR
_IMAGE_EXTS = frozenset({"png", "jpg", "jpeg", "webp", "gif", "bmp"})

#: 只用标准库（wave + audioop）就能转 PCM 的音频格式。
_WAV_ONLY_AUDIO_EXTS = frozenset({"wav"})

#: 需要 ffmpeg 才能转 PCM 的音视频格式。
#:
#: 音视频在这里是**同一条路**：kb_processor._to_pcm16k 用 ffmpeg 抽音轨，
#: 对 mp4 和 mp3 是同一个命令（-vn 丢掉视频流）。所以 parse_audio 同时服务两者，
#: 不需要单独的 parse_video。
_FFMPEG_AUDIO_EXTS = frozenset({"mp3", "m4a", "flac", "ogg", "aac", "wma", "opus"})
_FFMPEG_VIDEO_EXTS = frozenset({"mp4", "avi", "mov", "mkv", "webm", "flv", "wmv", "m4v"})


def _has_ffmpeg() -> bool:
    """ffmpeg 在不在。装在 lea-rag 镜像里（见 packages/lea-rag/Dockerfile）。"""
    import shutil

    return shutil.which("ffmpeg") is not None


def media_exts() -> frozenset[str]:
    """当前环境能转写的音视频格式。

    没有 ffmpeg 时只剩 WAV —— 标准库的 wave 模块只认它。这个判断必须是运行时
    的：镜像里有 ffmpeg，但本地开发和 CI 环境不一定有，写死会让白名单和实际
    能力不一致（宽了是"上传成功但解析失败"）。
    """
    if _has_ffmpeg():
        return _WAV_ONLY_AUDIO_EXTS | _FFMPEG_AUDIO_EXTS | _FFMPEG_VIDEO_EXTS
    return _WAV_ONLY_AUDIO_EXTS


#: 不依赖 MinerU 就能处理的格式。
#: txt/md 在 kb_processor._parse 里直接读文本，不进 parse_document。
_ALWAYS_SUPPORTED_EXTS = frozenset({
    "txt", "md",
    "docx", "pptx",                                  # python-docx / python-pptx
}) | _IMAGE_EXTS


def supported_upload_exts() -> frozenset[str]:
    """当前部署真正能解析的扩展名，给上传接口做白名单。

    必须从解析能力推导而不是在接口里写死一份：两处各写一遍的话迟早漂移，而
    漂移的两个方向都坏 —— 白名单宽了是"上传成功但解析失败"，窄了是明明能解析
    却被 400 拒。

    本地 MinerU 一配上，XLSX / EPUB / HTML / CSV / RTF / ODF 自动进白名单；
    装了 ffmpeg，mp3/m4a/flac/ogg 和 mp4/avi/mov/mkv 等自动进白名单。
    """
    return _ALWAYS_SUPPORTED_EXTS | mineru_native_exts() | media_exts()
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
    """本地自部署 MinerU（同步 /file_parse 接口）。

    名字里的 pdf 是历史包袱 —— 本地 MinerU 原生支持 Office / OpenDocument /
    EPUB / HTML / CSV 等（见 _MINERU_NATIVE_EXTS），这个函数对它们都适用，
    mime 按扩展名查表给。
    """
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    mime = _EXT_MIME.get(ext, "application/octet-stream")
    with open(file_path, "rb") as f:
        filename = os.path.basename(file_path)
        files = {"files": (filename, f, mime)}
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


#: 扩展名 → 本地兜底解析器。MinerU 不可用或失败时走这里。
#: 没有条目的格式（xlsx/epub/html/csv 等）只有 MinerU 这一条路。
_FALLBACK_PARSERS: dict[str, str] = {
    "docx": "parse_docx",
    "pptx": "parse_pptx",
}


def parse_document(file_path: str, parser: str = "mineru") -> dict:
    """统一入口。返回 {md_content, text_chunks, image_items}。

    ## 路由

    本地 MinerU 已配置（MINERU_URL）时，文档类格式一律优先交给它 —— 它的版面
    分析、表格结构和阅读顺序比 python-docx / python-pptx 那种"按 XML 顺序平铺"
    强得多，而且多覆盖 XLSX / EPUB / HTML / CSV / RTF / ODF 一批格式。

    云端模式下只有 PDF 走 MinerU：官方文档写明 Office 等格式**只用本地原生解析**
    （见 _MINERU_NATIVE_EXTS）。交给云端的后果不是报错而是拿到空内容 —— 那会让
    "上传成功但搜不到"再多一个来源。

    ## 兜底

    MinerU 失败一律降级到本地解析器，不让一次服务抖动变成文档丢失：
      pdf          → pymupdf
      docx / pptx  → python-docx / python-pptx
      其余格式     → 无兜底，抛出去由 kb_processor 记 error 状态

    图片和音频不进 MinerU，理由见 _MINERU_NATIVE_EXTS 的注释。
    """
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""

    # 图片：返回整图交给 VLM / multimodal，不做 OCR
    if ext in _IMAGE_EXTS:
        return parse_image(file_path)
    # 音视频：MinerU 不做 ASR。两者走同一条路 —— ffmpeg 抽音轨对 mp4 和 mp3
    # 是同一个命令，所以不需要单独的 parse_video。
    if ext in (_WAV_ONLY_AUDIO_EXTS | _FFMPEG_AUDIO_EXTS | _FFMPEG_VIDEO_EXTS):
        return parse_audio(file_path)

    use_mineru = parser == "mineru" and ext in mineru_native_exts()
    if use_mineru:
        try:
            return parse_pdf_mineru(file_path)
        except Exception as exc:
            logger.warning("mineru_fallback", ext=ext, error=str(exc))

    # 兜底（MinerU 没走成，或压根没让它走）
    fb = _FALLBACK_PARSERS.get(ext)
    if fb == "parse_docx":
        return parse_docx(file_path)
    if fb == "parse_pptx":
        return parse_pptx(file_path)

    if ext in ("pdf", ""):
        return parse_pdf_pymupdf(file_path)

    # 只有 MinerU 能处理的格式，而它失败了。往上抛而不是返回空 —— 空内容会被
    # 当成"解析成功但文档没内容"，文档状态变 ready，用户看不出有问题。
    raise RuntimeError(
        f"{ext} 只能由本地 MinerU 解析，而它不可用或解析失败"
        f"（MINERU_URL={'已配置' if mineru_local_url() else '未配置'}）"
    )


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
    """音视频的解析入口 —— 只交出文件路径，真正的转写在 kb_processor 里。

    **视频也走这里**：kb_processor._to_pcm16k 用 ffmpeg 抽音轨，对 mp4 和 mp3
    是同一个命令（-vn 丢掉视频流）。所以没有 parse_video，一条路服务两者。

    这里不直接转写，是因为 ASR 模型配置在 retrieval_models 表里，而 parser 层
    拿不到 db session。分工：parser 只认文件，kb_processor 负责调外部服务。
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
