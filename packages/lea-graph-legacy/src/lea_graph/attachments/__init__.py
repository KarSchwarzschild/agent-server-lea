"""聊天附件处理 — 文件上传后提取内容注入对话上下文。"""

from lea_graph.attachments.base import AttachmentProcessor, AttachmentResult
from lea_graph.attachments.registry import get_processor, process_attachment

__all__ = ["AttachmentProcessor", "AttachmentResult", "get_processor", "process_attachment"]
