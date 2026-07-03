"""文档生成工具 — PPT / Word 一次性生成。"""

from __future__ import annotations

import io
import json
import uuid

import structlog
from langchain_core.tools import tool
from langgraph.config import get_stream_writer

logger = structlog.get_logger(__name__)


@tool
async def generate_pptx(title: str, slides: str) -> str:
    """生成 PowerPoint 演示文稿。

    Args:
        title: 演示文稿标题
        slides: JSON 格式的幻灯片内容数组。每个元素: {"title": "标题", "bullets": ["要点1", "要点2"]}

    Returns:
        文件下载链接
    """
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor

    writer = get_stream_writer()
    writer({"type": "action", "tool": "generate-pptx", "input": {"title": title}})

    try:
        slide_data = json.loads(slides)
    except json.JSONDecodeError:
        return "slides 参数格式错误，需要 JSON 数组"

    prs = Presentation()
    prs.slide_width = Inches(13.33)
    prs.slide_height = Inches(7.5)

    # Title slide
    layout = prs.slide_layouts[0]
    slide = prs.slides.add_slide(layout)
    slide.shapes.title.text = title
    if slide.placeholders[1]:
        slide.placeholders[1].text = f"共 {len(slide_data)} 页"

    # Content slides
    layout = prs.slide_layouts[1]
    for item in slide_data:
        s = prs.slides.add_slide(layout)
        s.shapes.title.text = item.get("title", "")
        body = s.placeholders[1]
        tf = body.text_frame
        tf.clear()
        for bullet in item.get("bullets", []):
            p = tf.add_paragraph()
            p.text = bullet
            p.font.size = Pt(18)

    # Save to bytes
    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    file_bytes = buf.getvalue()

    # Store via object storage (MinIO)
    from lea_server.services.object_storage import get_object_storage
    filename = f"{title}_{uuid.uuid4().hex[:8]}.pptx"
    file_key = f"generated/{filename}"
    get_object_storage().put_object(
        file_key, file_bytes,
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )
    download_url = get_object_storage().get_presigned_url(file_key)

    writer({"type": "observation", "tool": "generate-pptx", "status": "success", "filename": filename})
    return f"PPT 已生成: {filename}\n下载链接: {download_url}"


@tool
async def generate_docx(title: str, content: str) -> str:
    """生成 Word 文档。

    Args:
        title: 文档标题
        content: Markdown 格式的文档内容。支持 # 标题、- 列表、普通段落。

    Returns:
        文件下载链接
    """
    from docx import Document
    from docx.shared import Pt

    writer = get_stream_writer()
    writer({"type": "action", "tool": "generate-docx", "input": {"title": title}})

    doc = Document()
    doc.add_heading(title, level=0)

    for line in content.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("# "):
            doc.add_heading(line[2:], level=1)
        elif line.startswith("## "):
            doc.add_heading(line[3:], level=2)
        elif line.startswith("### "):
            doc.add_heading(line[4:], level=3)
        elif line.startswith("- ") or line.startswith("* "):
            doc.add_paragraph(line[2:], style="List Bullet")
        else:
            doc.add_paragraph(line)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    file_bytes = buf.getvalue()

    from lea_server.services.object_storage import get_object_storage
    filename = f"{title}_{uuid.uuid4().hex[:8]}.docx"
    file_key = f"generated/{filename}"
    get_object_storage().put_object(
        file_key, file_bytes,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    download_url = get_object_storage().get_presigned_url(file_key)

    writer({"type": "observation", "tool": "generate-docx", "status": "success", "filename": filename})
    return f"Word 文档已生成: {filename}\n下载链接: {download_url}"
