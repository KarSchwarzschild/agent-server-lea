"""Pandoc 文档转换服务 — Markdown → Word/PDF/LaTeX。

无状态设计：每次调用实时转换，不缓存文件，任何实例都能处理。
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import structlog

logger = structlog.get_logger()

SUPPORTED_FORMATS = {
    "docx": {"ext": ".docx", "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    "pdf": {"ext": ".pdf", "mime": "application/pdf"},
    "latex": {"ext": ".tex", "mime": "application/x-tex"},
    "md": {"ext": ".md", "mime": "text/markdown"},
}


class PandocConverter:
    def __init__(self, reference_docx: str | None = None):
        self._reference_docx = reference_docx

    async def convert(self, markdown: str, fmt: str, title: str = "document") -> bytes:
        if fmt not in SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported format: {fmt}. Use: {list(SUPPORTED_FORMATS.keys())}")

        if fmt == "md":
            return markdown.encode("utf-8")

        ext = SUPPORTED_FORMATS[fmt]["ext"]

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.md"
            output_path = Path(tmpdir) / f"output{ext}"
            input_path.write_text(markdown, encoding="utf-8")

            cmd = [
                "pandoc", str(input_path),
                "-o", str(output_path),
                "--standalone",
                f"--metadata=title:{title}",
            ]

            if fmt == "pdf":
                cmd.extend(["--pdf-engine=xelatex", "-V", "CJKmainfont=SimSun"])
            elif fmt == "docx" and self._reference_docx:
                cmd.extend([f"--reference-doc={self._reference_docx}"])

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                error_msg = stderr.decode(errors="replace")
                logger.error("pandoc_failed", fmt=fmt, error=error_msg)
                raise RuntimeError(f"Pandoc conversion failed: {error_msg}")

            return output_path.read_bytes()

    @staticmethod
    def get_mime_type(fmt: str) -> str:
        return SUPPORTED_FORMATS.get(fmt, {}).get("mime", "application/octet-stream")

    @staticmethod
    def get_extension(fmt: str) -> str:
        return SUPPORTED_FORMATS.get(fmt, {}).get("ext", ".bin")
