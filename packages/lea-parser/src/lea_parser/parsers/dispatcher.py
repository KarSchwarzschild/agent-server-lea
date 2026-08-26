"""统一解析调度器 — 根据文件扩展名分发到对应 parser。"""

from __future__ import annotations

import structlog

from lea_parser.parsers.pdf import parse_pdf
from lea_parser.parsers.docx import parse_docx
from lea_parser.parsers.pptx_parser import parse_pptx
from lea_parser.parsers.text import parse_text
from lea_parser.parsers.image import parse_image
from lea_parser.parsers.audio import parse_audio
from lea_parser.parsers.video import parse_video

logger = structlog.get_logger()

_EXT_MAP = {
    "pdf": parse_pdf,
    "docx": parse_docx,
    "doc": parse_docx,
    "pptx": parse_pptx,
    "ppt": parse_pptx,
    "md": parse_text,
    "txt": parse_text,
    "csv": parse_text,
    "json": parse_text,
    "png": parse_image,
    "jpg": parse_image,
    "jpeg": parse_image,
    "webp": parse_image,
    "gif": parse_image,
    "bmp": parse_image,
    "mp3": parse_audio,
    "wav": parse_audio,
    "m4a": parse_audio,
    "flac": parse_audio,
    "ogg": parse_audio,
    "mp4": parse_video,
    "avi": parse_video,
    "mkv": parse_video,
    "mov": parse_video,
    "wmv": parse_video,
    "flv": parse_video,
    "webm": parse_video,
}

SUPPORTED_FORMATS = set(_EXT_MAP.keys())


def parse_document(file_path: str) -> dict:
    """统一入口。返回 {md_content, text_blocks, image_items}。"""
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    parser_fn = _EXT_MAP.get(ext)
    if not parser_fn:
        logger.warning("unsupported_format", ext=ext, file=file_path)
        return parse_text(file_path)
    return parser_fn(file_path)
