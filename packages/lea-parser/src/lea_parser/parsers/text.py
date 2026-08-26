"""纯文本/Markdown 解析 — 直接读取。"""

from __future__ import annotations

import os


def parse_text(file_path: str) -> dict:
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    text_blocks = [{"text": content, "page_idx": 0}] if content.strip() else []
    return {"md_content": content, "text_blocks": text_blocks, "image_items": []}
