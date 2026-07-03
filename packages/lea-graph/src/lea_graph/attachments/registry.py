"""附件处理器注册表 — 按扩展名路由到对应处理器。"""

from __future__ import annotations

from lea_graph.attachments.base import AttachmentProcessor, AttachmentResult
from lea_graph.attachments.image import ImageProcessor
from lea_graph.attachments.text import TextDocProcessor

_processors: list[AttachmentProcessor] = [
    TextDocProcessor(),
    ImageProcessor(),
]

_extension_map: dict[str, AttachmentProcessor] | None = None


def _build_map() -> dict[str, AttachmentProcessor]:
    global _extension_map
    if _extension_map is None:
        _extension_map = {}
        for proc in _processors:
            for ext in proc.supported_extensions:
                _extension_map[ext] = proc
    return _extension_map


def get_processor(filename: str) -> AttachmentProcessor | None:
    """根据文件名获取对应的处理器，不支持的格式返回 None。"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return _build_map().get(ext)


def get_supported_extensions() -> list[str]:
    """获取所有支持的文件扩展名。"""
    return list(_build_map().keys())


async def process_attachment(file_content: bytes, filename: str) -> AttachmentResult:
    """处理附件文件，返回提取结果。不支持的格式抛出 ValueError。"""
    processor = get_processor(filename)
    if processor is None:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "(无)"
        supported = ", ".join(sorted(get_supported_extensions()))
        raise ValueError(f"不支持的文件类型: .{ext}，支持: {supported}")
    return await processor.extract(file_content, filename)
