"""SlideExport — SVG → PPTX/ZIP 导出。

将 SVG 幻灯片转为可下载的文件格式：
- PPTX: SVG 内容嵌入为图片（需要 cairosvg 转 PNG）
- ZIP: 所有 SVG 文件打包
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile

import structlog

logger = structlog.get_logger(__name__)


async def export_slides_as_zip(slides: list[str], title: str = "slides") -> tuple[bytes, str]:
    """将 SVG 列表打包为 ZIP。返回 (bytes, filename)。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, svg in enumerate(slides):
            zf.writestr(f"slide_{i + 1:02d}.svg", svg.encode("utf-8"))
    buf.seek(0)
    filename = f"{title}_{uuid.uuid4().hex[:8]}.zip"
    return buf.getvalue(), filename


async def export_slides_as_pptx(slides: list[str], title: str = "slides") -> tuple[bytes, str]:
    """将 SVG 列表转为 PPTX（每页一张全屏图片）。"""
    try:
        import cairosvg
    except ImportError:
        logger.warning("cairosvg_not_available, falling back to zip")
        return await export_slides_as_zip(slides, title)

    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    prs.slide_width = Inches(13.33)
    prs.slide_height = Inches(7.5)

    blank_layout = prs.slide_layouts[6]

    for svg in slides:
        png_bytes = cairosvg.svg2png(
            bytestring=svg.encode("utf-8"),
            output_width=1920,
            output_height=1080,
        )
        slide = prs.slides.add_slide(blank_layout)
        img_stream = io.BytesIO(png_bytes)
        slide.shapes.add_picture(img_stream, Inches(0), Inches(0), Inches(13.33), Inches(7.5))

    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    filename = f"{title}_{uuid.uuid4().hex[:8]}.pptx"
    return buf.getvalue(), filename
