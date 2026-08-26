"""
安全策略引擎。

集中定义：路径白名单、工具黑名单、敏感文件模式、文件大小限制。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

SENSITIVE_FILE_PATTERNS = [
    r"\.env($|\.)",
    r"credentials",
    r"secret",
    r"\.pem$",
    r"\.key$",
    r"id_rsa",
    r"\.p12$",
    r"\.pfx$",
    r"token\.json$",
]

DANGEROUS_TOOLS = frozenset({
    "exec_shell",
    "run_python",
    "eval",
    "run_sql",
    "drop_table",
    "delete_database",
    "system_command",
})

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB


@dataclass
class SecurityPolicy:
    """安全策略配置。"""

    allowed_base_dirs: list[Path] = field(default_factory=list)
    blocked_tools: frozenset[str] = DANGEROUS_TOOLS
    sensitive_patterns: list[str] = field(default_factory=lambda: list(SENSITIVE_FILE_PATTERNS))
    max_file_size: int = MAX_FILE_SIZE_BYTES
    allow_symlinks: bool = False

    def is_path_allowed(self, target: str | Path) -> bool:
        """检查路径是否在白名单目录内。"""
        target = Path(target).resolve()

        if not self.allow_symlinks and target != Path(target):
            return False

        if not self.allowed_base_dirs:
            return True

        return any(self._is_subpath(target, base) for base in self.allowed_base_dirs)

    def is_sensitive_file(self, filename: str) -> bool:
        """检查是否为敏感文件。"""
        name_lower = filename.lower()
        return any(re.search(pattern, name_lower) for pattern in self.sensitive_patterns)

    def is_tool_blocked(self, tool_name: str) -> bool:
        return tool_name in self.blocked_tools

    @staticmethod
    def _is_subpath(child: Path, parent: Path) -> bool:
        try:
            child.resolve().relative_to(parent.resolve())
            return True
        except ValueError:
            return False
