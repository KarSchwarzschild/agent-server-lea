"""视频解析 — 调用外部字幕提取服务，解析 SRT/VTT 为文本块。"""

from __future__ import annotations

import os
import re

import httpx
import structlog

from lea_parser.config import get_service_settings

logger = structlog.get_logger()

_TIMESTAMP_RE = re.compile(
    r"(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})"
)


def parse_video(file_path: str) -> dict:
    """提取视频字幕并转为文本块。"""
    settings = get_service_settings()
    if not settings.subtitle_service_url:
        return _fallback_meta(file_path)

    try:
        subtitle_text = _extract_subtitles(
            file_path, settings.subtitle_service_url
        )
        text_blocks = _srt_to_blocks(subtitle_text)
        md = "\n".join(b["text"] for b in text_blocks)
        return {"md_content": md, "text_blocks": text_blocks, "image_items": []}
    except Exception as e:
        logger.warning("video_parse_fallback", error=str(e))
        return _fallback_meta(file_path)


def _extract_subtitles(file_path: str, service_url: str) -> str:
    """调用字幕提取服务，返回 SRT/VTT 格式字幕文本。"""
    with open(file_path, "rb") as f:
        filename = os.path.basename(file_path)
        resp = httpx.post(
            f"{service_url.rstrip('/')}/extract",
            files={"file": (filename, f, "video/mp4")},
            timeout=600,
        )
    resp.raise_for_status()
    data = resp.json()
    return data.get("subtitle", "") or data.get("text", "")


def _srt_to_blocks(subtitle_text: str) -> list[dict]:
    """将 SRT/VTT 字幕文本解析为 text_blocks。按时间段分块。"""
    lines = subtitle_text.strip().split("\n")
    blocks: list[dict] = []
    current_text: list[str] = []
    segment_idx = 0

    for line in lines:
        line = line.strip()
        if not line or line.isdigit() or line.startswith("WEBVTT"):
            if current_text:
                text = " ".join(current_text).strip()
                if text:
                    blocks.append({"text": text, "page_idx": segment_idx})
                    segment_idx += 1
                current_text = []
            continue
        if _TIMESTAMP_RE.match(line):
            if current_text:
                text = " ".join(current_text).strip()
                if text:
                    blocks.append({"text": text, "page_idx": segment_idx})
                    segment_idx += 1
                current_text = []
            continue
        line = re.sub(r"<[^>]+>", "", line)
        current_text.append(line)

    if current_text:
        text = " ".join(current_text).strip()
        if text:
            blocks.append({"text": text, "page_idx": segment_idx})

    return _merge_short_blocks(blocks)


def _merge_short_blocks(blocks: list[dict], min_len: int = 20) -> list[dict]:
    """合并过短的字幕段。"""
    if not blocks:
        return []
    merged: list[dict] = []
    current = blocks[0]["text"]
    page = blocks[0]["page_idx"]
    for b in blocks[1:]:
        if len(current) < min_len:
            current += " " + b["text"]
        else:
            merged.append({"text": current, "page_idx": page})
            current = b["text"]
            page = b["page_idx"]
    if current:
        merged.append({"text": current, "page_idx": page})
    return merged


def _fallback_meta(file_path: str) -> dict:
    """无字幕服务时返回元信息。"""
    filename = os.path.basename(file_path)
    return {
        "md_content": "",
        "text_blocks": [],
        "image_items": [],
        "video_meta": {
            "filename": filename,
            "size": os.path.getsize(file_path),
        },
    }
