"""文档解析 — PDF / 纯文本 → LlamaIndex Document。"""

from __future__ import annotations

from pathlib import Path

import structlog
from llama_index.core.schema import Document

logger = structlog.get_logger(__name__)


def load_pdf(path: str | Path, *, metadata: dict | None = None) -> list[Document]:
    """用 PyMuPDF 解析 PDF，每页一个 Document。"""
    import fitz  # PyMuPDF

    path = Path(path)
    docs: list[Document] = []
    pdf = fitz.open(str(path))

    for page_num, page in enumerate(pdf, 1):
        text = page.get_text("text").strip()
        if not text:
            continue
        meta = {"source": path.name, "page": page_num, **(metadata or {})}
        docs.append(Document(text=text, metadata=meta))

    pdf.close()
    logger.info("pdf_loaded", path=str(path), pages=len(docs))
    return docs


def load_text(path: str | Path, *, metadata: dict | None = None) -> list[Document]:
    """纯文本文件 → 单个 Document。"""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    meta = {"source": path.name, **(metadata or {})}
    return [Document(text=text, metadata=meta)]


def load_file(path: str | Path, **kwargs) -> list[Document]:
    """根据后缀自动选择加载器。"""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return load_pdf(path, **kwargs)
    if suffix in (".txt", ".md", ".rst"):
        return load_text(path, **kwargs)
    raise ValueError(f"Unsupported file type: {suffix}")
