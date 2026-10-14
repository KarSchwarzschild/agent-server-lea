"""DOCX 解析 — python-docx 提取段落/表格/图片。"""

from __future__ import annotations

import base64

from docx import Document as DocxDocument


def parse_docx(file_path: str) -> dict:
    doc = DocxDocument(file_path)
    text_blocks: list[dict] = []
    image_items: list[dict] = []

    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            text_blocks.append({"text": text, "page_idx": 0})

    for table in doc.tables:
        rows = [" | ".join(c.text.strip() for c in row.cells) for row in table.rows]
        table_text = "\n".join(rows)
        if table_text.strip():
            text_blocks.append({"text": f"表格：\n{table_text}", "page_idx": 0})

    for rel in doc.part.rels.values():
        if "image" in rel.reltype:
            try:
                b64 = base64.b64encode(rel.target_part.blob).decode()
                image_items.append({
                    "base64": b64, "page_idx": 0,
                    "caption": "", "img_path": rel.target_ref,
                })
            except Exception:
                pass

    md = "\n\n".join(b["text"] for b in text_blocks)
    return {"md_content": md, "text_blocks": text_blocks, "image_items": image_items}
