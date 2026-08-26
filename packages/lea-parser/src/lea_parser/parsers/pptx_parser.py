"""PPTX 解析 — python-pptx 逐 slide 提取文本/表格/图片。"""

from __future__ import annotations

import base64

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE


def parse_pptx(file_path: str) -> dict:
    prs = Presentation(file_path)
    text_blocks: list[dict] = []
    image_items: list[dict] = []

    for slide_idx, slide in enumerate(prs.slides):
        slide_texts: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    t = para.text.strip()
                    if t:
                        slide_texts.append(t)
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                try:
                    b64 = base64.b64encode(shape.image.blob).decode()
                    image_items.append({
                        "base64": b64, "page_idx": slide_idx,
                        "caption": "", "img_path": f"slide_{slide_idx+1}.png",
                    })
                except Exception:
                    pass
            if shape.has_table:
                rows = [" | ".join(c.text.strip() for c in row.cells)
                        for row in shape.table.rows]
                if any(rows):
                    slide_texts.append(f"表格：\n" + "\n".join(rows))
        if slide_texts:
            text_blocks.append({"text": "\n".join(slide_texts), "page_idx": slide_idx})

    md = "\n\n".join(b["text"] for b in text_blocks)
    return {"md_content": md, "text_blocks": text_blocks, "image_items": image_items}
