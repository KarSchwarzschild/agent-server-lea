"""文本类文件处理器 — PDF / docx / txt / md / csv。"""

from __future__ import annotations

import io

import structlog

from lea_graph.attachments.base import AttachmentProcessor, AttachmentResult

logger = structlog.get_logger(__name__)

MAX_CHARS = 30000


class TextDocProcessor(AttachmentProcessor):
    """处理文本类文档，提取全文。"""

    supported_extensions = ["pdf", "docx", "txt", "md", "csv"]

    async def extract(self, file_content: bytes, filename: str) -> AttachmentResult:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

        if ext == "pdf":
            text, page_count = self._extract_pdf(file_content)
            metadata = {"pages": page_count}
        elif ext == "docx":
            text = self._extract_docx(file_content)
            metadata = {}
        elif ext == "csv":
            text = self._extract_csv(file_content)
            metadata = {}
        else:
            text = file_content.decode("utf-8", errors="replace")
            metadata = {}

        truncated = len(text) > MAX_CHARS
        if truncated:
            text = text[:MAX_CHARS] + "\n\n[... 内容过长，已截断 ...]"

        logger.info(
            "attachment_extracted",
            filename=filename,
            chars=len(text),
            truncated=truncated,
        )
        return AttachmentResult(
            text=text,
            media_type="text",
            metadata=metadata,
            truncated=truncated,
        )

    def _extract_pdf(self, content: bytes) -> tuple[str, int]:
        import fitz

        pdf = fitz.open(stream=content, filetype="pdf")
        pages: list[str] = []
        for page in pdf:
            t = page.get_text("text").strip()
            if t:
                pages.append(t)
        pdf.close()
        return "\n\n".join(pages), len(pages)

    def _extract_docx(self, content: bytes) -> str:
        from docx import Document

        doc = Document(io.BytesIO(content))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n\n".join(paragraphs)

    def _extract_csv(self, content: bytes) -> str:
        text = content.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if len(lines) > 200:
            return "\n".join(lines[:200]) + f"\n\n[... 共 {len(lines)} 行，仅显示前200行 ...]"
        return text
