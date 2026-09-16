"""PDF 解析 — MinerU 优先，PyMuPDF fallback。"""

from __future__ import annotations

import io
import json
import os
import re
import time
import zipfile

import httpx
import structlog

from lea_parser.config import get_service_settings

logger = structlog.get_logger()


def parse_pdf(file_path: str) -> dict:
    settings = get_service_settings()
    if settings.mineru_url:
        try:
            return _parse_mineru_local(file_path, settings.mineru_url)
        except Exception as e:
            logger.warning("mineru_local_fallback", error=str(e))
    elif settings.mineru_token:
        try:
            return _parse_mineru_cloud(file_path, settings.mineru_token)
        except Exception as e:
            logger.warning("mineru_cloud_fallback", error=str(e))
    return _parse_pymupdf(file_path)


def _parse_pymupdf(file_path: str) -> dict:
    """兜底路径：只抽**文本层**，不做 OCR。

    ⚠️ 这条路拿不到扫描件的内容。扫描版 PDF 由 MinerU 处理（它带 OCR / VLM，
    parse_pdf 优先走那边），这里是 MinerU 没配或调用失败时的降级。

    所以 `page_count > 0 而 md 为空` 在这条路上的含义是**"要么是扫描件、要么这几页
    真的没字"，而我们分不出来** —— 因为没有 OCR 就没有判据。上游据此提示模型"文本层
    为空，可在沙箱里 OCR"，而不是让它以为文件是空的。

    真正该修的是让 MinerU 别静默降级到这里（见 parse_pdf 那两个 except 分支只 warning），
    但那是部署可观测性的事，不在这一轮。
    """
    import fitz
    doc = fitz.open(file_path)
    text_blocks: list[dict] = []
    page_count = doc.page_count
    for page_num, page in enumerate(doc):
        text = page.get_text().strip()
        if text:
            text_blocks.append({"text": text, "page_idx": page_num})
    doc.close()
    md = "\n\n".join(b["text"] for b in text_blocks)
    return {"md_content": md, "text_blocks": text_blocks, "image_items": [],
            "page_count": page_count}


def _html_table_to_text(html: str) -> str:
    html = re.sub(r"<tr>", "\n", html)
    html = re.sub(r"</?(table|tbody|thead|tfoot)[^>]*>", "", html)
    html = re.sub(r"<(td|th)[^>]*>", " | ", html)
    html = re.sub(r"</(td|th|tr)>", "", html)
    html = re.sub(r"<[^>]+>", "", html)
    lines = [line.strip().strip("|").strip() for line in html.split("\n")]
    return "\n".join(line for line in lines if line)


def _parse_content_list(content_list, images_dict: dict) -> dict:
    items = json.loads(content_list) if isinstance(content_list, str) else content_list
    text_blocks: list[dict] = []
    image_items: list[dict] = []
    max_page = -1
    for item in items:
        item_type = item.get("type", "")
        page_idx = item.get("page_idx", 0)
        max_page = max(max_page, int(page_idx))
        if item_type == "text":
            text = item.get("text", "").strip()
            if text:
                text_blocks.append({"text": text, "page_idx": page_idx})
        elif item_type == "table":
            table_text = _html_table_to_text(item.get("table_body", ""))
            caption = (item.get("table_caption") or [""])[0]
            if caption:
                table_text = f"表格：{caption}\n{table_text}"
            if table_text.strip():
                text_blocks.append({"text": table_text, "page_idx": page_idx})
        elif item_type == "image":
            img_path = item.get("img_path", "")
            img_name = img_path.split("/")[-1] if "/" in img_path else img_path
            b64 = images_dict.get(img_name, "")
            if b64:
                image_items.append({
                    "base64": b64, "page_idx": page_idx,
                    "caption": (item.get("image_caption") or [""])[0],
                    "img_path": img_path,
                })
    # page_count 从 content_list 里最大的 page_idx 推。MinerU 不单独给总页数，而纯图片页
    # 在 content_list 里仍有 image 条目（带 page_idx），所以这个推断通常是准的。
    return {
        "text_blocks": text_blocks,
        "image_items": image_items,
        "page_count": max_page + 1 if max_page >= 0 else 0,
    }


def _page_count_of(parsed: dict) -> int:
    """content_list 推出来的页数。0 = 推不出来。

    ## 为什么不用它去重拼 md_content

    试过让 md 按页重排（加 `[第 N 页]` 标记），已经回退：MinerU 返回的 md_content 排版
    显著更好（标题层级、列表、公式），而按 text_blocks 重拼会把这些全拍平成纯文本。

    而重拼换来的东西其实没有必要：模型改文件是在沙箱里 `prs.slides[i]` / `doc.pages[i]`
    直接定位的，那才是真相；就算用户说的是"改讲卷积的那一页"，模型也可以在沙箱里逐页
    dump 文本自己找，比从注入文本里数段落可靠。所以页码标记只省一次往返，代价是丢掉
    整篇排版 —— 不值。

    page_count 本身留着：注入文本里要报"共 N 页"，那是纯元信息，不动正文。
    """
    return parsed.get("page_count") or 0


def _parse_mineru_local(file_path: str, base_url: str) -> dict:
    with open(file_path, "rb") as f:
        filename = os.path.basename(file_path)
        files = {"files": (filename, f, "application/pdf")}
        data = {"lang_list": '["ch"]', "return_md": "true",
                "return_content_list": "true", "return_images": "true"}
        resp = httpx.post(f"{base_url.rstrip('/')}/file_parse",
                          files=files, data=data, timeout=300)
    resp.raise_for_status()
    result = resp.json()
    file_key = list(result["results"].keys())[0]
    file_result = result["results"][file_key]
    md = file_result.get("md_content", "")
    cl = file_result.get("content_list")
    imgs = file_result.get("images", {})
    if cl:
        parsed = _parse_content_list(cl, imgs)
        return {"md_content": md,
                "text_blocks": parsed["text_blocks"],
                "image_items": parsed["image_items"],
                "page_count": _page_count_of(parsed)}
    return {"md_content": md, "text_blocks": [{"text": md, "page_idx": 0}],
            "image_items": [], "page_count": 0}


_MINERU_API_BASE = "https://mineru.net/api/v4"


def _parse_mineru_cloud(file_path: str, token: str) -> dict:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    filename = os.path.basename(file_path)
    batch_resp = httpx.post(
        f"{_MINERU_API_BASE}/file-urls/batch", headers=headers,
        json={"files": [{"name": filename, "is_ocr": False}], "model_version": "vlm"},
        timeout=30)
    batch_resp.raise_for_status()
    batch_data = batch_resp.json()
    if batch_data.get("code") != 0:
        raise RuntimeError(f"MinerU batch failed: {batch_data}")
    batch_id = batch_data["data"]["batch_id"]
    upload_url = batch_data["data"]["file_urls"][0]
    with open(file_path, "rb") as f:
        httpx.put(upload_url, content=f.read(), timeout=120).raise_for_status()
    zip_url = _poll_done(batch_id, token)
    return _download_zip(zip_url, token)


def _poll_done(batch_id: str, token: str, timeout: int = 300) -> str:
    headers = {"Authorization": f"Bearer {token}"}
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = httpx.get(f"{_MINERU_API_BASE}/extract-results/batch/{batch_id}",
                         headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        results = data.get("extract_result") or data.get("file_results") or []
        if results and results[0].get("state") == "done":
            return results[0].get("full_zip_url") or batch_id
        if results and results[0].get("state") == "failed":
            raise RuntimeError(f"MinerU failed: {results[0]}")
        time.sleep(3)
    raise RuntimeError(f"MinerU timeout ({timeout}s)")


def _download_zip(zip_url: str, token: str) -> dict:
    import base64
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
                img_name = name.split("/")[-1]
                images_dict[img_name] = base64.b64encode(zf.read(name)).decode()
    if content_list_raw:
        parsed = _parse_content_list(content_list_raw, images_dict)
        return {"md_content": md_content,
                "text_blocks": parsed["text_blocks"],
                "image_items": parsed["image_items"],
                "page_count": _page_count_of(parsed)}
    return {"md_content": md_content,
            "text_blocks": [{"text": md_content, "page_idx": 0}] if md_content else [],
            "image_items": [], "page_count": 0}
