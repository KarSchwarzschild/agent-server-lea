"""
代码执行沙盒 — subprocess 隔离执行用户代码。

安全措施：
- 独立子进程（不共享主进程内存）
- 执行超时限制
- 禁止导入危险模块
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger()

FORBIDDEN_IMPORTS = frozenset({
    "os", "sys", "subprocess", "shutil",
    "socket", "http", "urllib", "requests",
    "ctypes", "importlib", "pickle",
})


@dataclass
class ExecutionResult:
    stdout: str
    stderr: str
    return_code: int
    timed_out: bool = False


class CodeExecutor:
    """Python 代码沙盒执行器。"""

    def __init__(self, timeout: int = 30, max_output_size: int = 100_000):
        self.timeout = timeout
        self.max_output_size = max_output_size

    def check_code_safety(self, code: str) -> list[str]:
        """静态检查代码安全性，返回违规列表。"""
        violations = []
        for module in FORBIDDEN_IMPORTS:
            if f"import {module}" in code or f"from {module}" in code:
                violations.append(f"forbidden import: {module}")
        if "exec(" in code or "eval(" in code:
            violations.append("exec/eval calls not allowed")
        if "__import__" in code:
            violations.append("__import__ not allowed")
        return violations

    def execute(self, code: str, working_dir: str | Path | None = None) -> ExecutionResult:
        """在隔离子进程中执行 Python 代码。"""
        violations = self.check_code_safety(code)
        if violations:
            return ExecutionResult(
                stdout="",
                stderr=f"Security violations: {'; '.join(violations)}",
                return_code=-1,
            )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(code)
            script_path = f.name

        try:
            result = subprocess.run(
                ["python", script_path],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=working_dir,
                env={"PATH": ""},
            )
            return ExecutionResult(
                stdout=result.stdout[:self.max_output_size],
                stderr=result.stderr[:self.max_output_size],
                return_code=result.returncode,
            )
        except subprocess.TimeoutExpired:
            logger.warning("code_execution_timeout", timeout=self.timeout)
            return ExecutionResult(
                stdout="",
                stderr=f"Execution timed out after {self.timeout}s",
                return_code=-1,
                timed_out=True,
            )
        finally:
            Path(script_path).unlink(missing_ok=True)
