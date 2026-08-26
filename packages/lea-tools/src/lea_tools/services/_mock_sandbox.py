"""Mock sandbox 用于开发/CI，不依赖外部服务。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class MockExecution:
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    artifacts: list[str] = field(default_factory=list)
    duration_ms: int = 0


class MockSandbox:
    def __init__(self):
        self.sandbox_id = f"mock-{uuid.uuid4().hex[:8]}"
        self._workdir = Path(tempfile.mkdtemp(prefix=f"lea-sbx-{self.sandbox_id}-"))
        self._known: set[str] = set()

    def run_code(self, code: str, language: str = "python", timeout: int = 60) -> MockExecution:
        pre = {str(p) for p in self._workdir.rglob("*") if p.is_file()}
        t0 = time.time()

        if language == "python":
            argv = [sys.executable, "-c", code]
        elif language in ("shell", "bash"):
            argv = ["bash", "-c", code] if shutil.which("bash") else ["cmd", "/c", code]
        else:
            return MockExecution(exit_code=2, stderr=f"unsupported: {language}")

        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        try:
            proc = subprocess.run(
                argv, cwd=self._workdir, capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=timeout, env=env,
            )
            stdout, stderr, code_rc = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as e:
            return MockExecution(
                stdout=e.stdout or "", stderr=f"timeout after {timeout}s",
                exit_code=124, duration_ms=int((time.time() - t0) * 1000),
            )

        post = {str(p) for p in self._workdir.rglob("*") if p.is_file()}
        new_files = sorted(post - pre - self._known)
        self._known.update(new_files)

        return MockExecution(
            stdout=stdout, stderr=stderr, exit_code=code_rc,
            artifacts=new_files,
            duration_ms=int((time.time() - t0) * 1000),
        )

    def write_file(self, path: str, data: bytes) -> None:
        dest = (self._workdir / path.lstrip("/")).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    def read_file(self, path: str) -> bytes:
        return (self._workdir / path.lstrip("/")).read_bytes()

    def kill(self) -> None:
        shutil.rmtree(self._workdir, ignore_errors=True)
