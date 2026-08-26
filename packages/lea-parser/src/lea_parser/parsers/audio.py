"""音频解析 — 调用 ASR 服务转写。"""

from __future__ import annotations

import os

import httpx
import structlog

from lea_parser.config import get_service_settings

logger = structlog.get_logger()


def parse_audio(file_path: str) -> dict:
    settings = get_service_settings()
    if settings.asr_url:
        try:
            return _transcribe(file_path, settings.asr_url, settings.asr_token)
        except Exception as e:
            logger.warning("asr_fallback", error=str(e))

    filename = os.path.basename(file_path)
    return {
        "md_content": "",
        "text_blocks": [],
        "image_items": [],
        "audio_meta": {"filename": filename, "size": os.path.getsize(file_path)},
    }


def _transcribe(file_path: str, asr_url: str, token: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with open(file_path, "rb") as f:
        filename = os.path.basename(file_path)
        resp = httpx.post(
            f"{asr_url.rstrip('/')}/v1/audio/transcriptions",
            headers=headers,
            files={"file": (filename, f, "audio/mpeg")},
            data={"model": "whisper-1", "language": "zh"},
            timeout=300,
        )
    resp.raise_for_status()
    text = resp.json().get("text", "")
    text_blocks = [{"text": text, "page_idx": 0}] if text else []
    return {"md_content": text, "text_blocks": text_blocks, "image_items": []}
