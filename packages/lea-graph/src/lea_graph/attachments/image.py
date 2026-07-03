"""图片处理器 — 后续扩展，当前转 base64 描述。"""

from __future__ import annotations

import base64

from lea_graph.attachments.base import AttachmentProcessor, AttachmentResult

MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB


class ImageProcessor(AttachmentProcessor):
    """处理图片文件。当前返回 base64，后续可接 VLM 生成描述。"""

    supported_extensions = ["jpg", "jpeg", "png", "gif", "webp"]

    async def extract(self, file_content: bytes, filename: str) -> AttachmentResult:
        if len(file_content) > MAX_IMAGE_SIZE:
            return AttachmentResult(
                text="[图片文件过大，无法处理]",
                media_type="image",
                metadata={"size": len(file_content)},
                truncated=True,
            )

        b64 = base64.b64encode(file_content).decode("ascii")
        ext = filename.rsplit(".", 1)[-1].lower()
        mime = f"image/{ext}" if ext != "jpg" else "image/jpeg"

        return AttachmentResult(
            text=f"[图片: {filename}]",
            media_type="image",
            metadata={"base64": b64, "mime_type": mime, "size": len(file_content)},
            truncated=False,
        )
