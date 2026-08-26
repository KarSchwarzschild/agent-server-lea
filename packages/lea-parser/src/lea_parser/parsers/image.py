"""图片解析 — 直接 base64 编码，OCR 由调用方决定。"""

from __future__ import annotations

import base64
import os


def parse_image(file_path: str) -> dict:
    with open(file_path, "rb") as f:
        img_data = f.read()
    b64 = base64.b64encode(img_data).decode()
    filename = os.path.basename(file_path)
    return {
        "md_content": "",
        "text_blocks": [],
        "image_items": [{
            "base64": b64,
            "page_idx": 0,
            "caption": "",
            "img_path": filename,
        }],
    }
