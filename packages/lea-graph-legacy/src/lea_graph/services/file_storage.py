"""文件存储服务 — 跨App文件/资源共享。"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()


class FileStorage:
    """基于本地文件系统的存储（生产环境可替换为OSS）。"""

    def __init__(self, base_dir: str | None = None):
        self._base_dir = Path(base_dir or os.getenv("GENERATED_FILES_DIR", "./data/generated_files"))
        self._base_dir.mkdir(parents=True, exist_ok=True)

    async def save(
        self,
        content: str,
        filename: str,
        metadata: dict[str, Any] | None = None,
        created_by_app: str = "platform",
    ) -> str:
        """保存文件，返回 file_id。"""
        file_id = str(uuid.uuid4())
        file_path = self._base_dir / f"{file_id}_{filename}"
        file_path.write_text(content, encoding="utf-8")
        logger.info("file_saved", file_id=file_id, filename=filename, app=created_by_app)
        return file_id

    async def get(self, file_id: str) -> dict[str, Any] | None:
        """读取文件。"""
        for path in self._base_dir.iterdir():
            if path.name.startswith(file_id):
                return {
                    "file_id": file_id,
                    "filename": path.name.split("_", 1)[1] if "_" in path.name else path.name,
                    "content": path.read_text(encoding="utf-8"),
                }
        return None

    async def list_files(self, app_filter: str | None = None) -> list[dict[str, str]]:
        """列出文件。"""
        files = []
        for path in self._base_dir.iterdir():
            if path.is_file():
                parts = path.name.split("_", 1)
                files.append({
                    "file_id": parts[0],
                    "filename": parts[1] if len(parts) > 1 else path.name,
                })
        return files


_storage: FileStorage | None = None


def get_file_storage() -> FileStorage:
    global _storage
    if _storage is None:
        _storage = FileStorage()
    return _storage
