"""
文件操作守卫 — 安全的文件读写封装。

所有文件操作经过安全策略检查：路径白名单、敏感文件保护、大小限制、Symlink 检测。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from lea_graph.sandbox.security import SecurityPolicy

logger = structlog.get_logger()


class FileAccessDenied(Exception):
    def __init__(self, path: str, reason: str):
        self.path = path
        self.reason = reason
        super().__init__(f"File access denied: {path} — {reason}")


class FileGuard:
    """安全文件操作守卫。"""

    def __init__(self, policy: SecurityPolicy):
        self._policy = policy

    def check_read(self, path: str | Path) -> Path:
        """检查读取权限，返回解析后的安全路径。"""
        resolved = Path(path).resolve()

        if not resolved.exists():
            raise FileAccessDenied(str(path), "file does not exist")

        if not self._policy.allow_symlinks and resolved.is_symlink():
            raise FileAccessDenied(str(path), "symlinks are not allowed")

        if not self._policy.is_path_allowed(resolved):
            raise FileAccessDenied(str(path), "path outside allowed directories")

        if self._policy.is_sensitive_file(resolved.name):
            raise FileAccessDenied(str(path), "sensitive file pattern detected")

        return resolved

    def check_write(self, path: str | Path, content_size: int = 0) -> Path:
        """检查写入权限。"""
        resolved = Path(path).resolve()

        if not self._policy.is_path_allowed(resolved):
            raise FileAccessDenied(str(path), "path outside allowed directories")

        if self._policy.is_sensitive_file(resolved.name):
            raise FileAccessDenied(str(path), "cannot write to sensitive file pattern")

        if content_size > self._policy.max_file_size:
            raise FileAccessDenied(
                str(path),
                f"content size {content_size} exceeds limit {self._policy.max_file_size}",
            )

        return resolved

    def read_file_safe(self, path: str | Path, encoding: str = "utf-8") -> str:
        resolved = self.check_read(path)
        logger.info("file_read", path=str(resolved))
        return resolved.read_text(encoding=encoding)

    def write_file_safe(self, path: str | Path, content: str, encoding: str = "utf-8") -> Path:
        content_bytes = content.encode(encoding)
        resolved = self.check_write(path, len(content_bytes))
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding=encoding)
        logger.info("file_written", path=str(resolved), size=len(content_bytes))
        return resolved

    def glob_safe(self, directory: str | Path, pattern: str = "*") -> list[Path]:
        """安全的 glob 操作 — 只在白名单目录内搜索。"""
        dir_path = Path(directory).resolve()

        if not self._policy.is_path_allowed(dir_path):
            raise FileAccessDenied(str(directory), "directory outside allowed paths")

        return [
            p for p in dir_path.glob(pattern)
            if not self._policy.is_sensitive_file(p.name)
        ]
