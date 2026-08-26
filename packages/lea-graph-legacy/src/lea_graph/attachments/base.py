"""附件处理器抽象基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class AttachmentResult:
    """附件处理结果。"""

    text: str
    media_type: str = "text"
    metadata: dict = field(default_factory=dict)
    truncated: bool = False


class AttachmentProcessor(ABC):
    """附件处理器基类。每种文件类型一个实现。"""

    supported_extensions: list[str] = []

    @abstractmethod
    async def extract(self, file_content: bytes, filename: str) -> AttachmentResult:
        """提取文件内容，返回可注入对话的文本表示。"""
