"""沙箱后端 —— E2B 协议兼容层，支持 Mock 和 E2B/CubeSandbox 两种后端。

工厂 `get_backend()` 按 SANDBOX_BACKEND 环境变量分派：
- mock: 本地 subprocess + tempdir，用于开发/CI，不依赖外部服务
- e2b:  走 e2b_code_interpreter SDK，兼容 CubeSandbox（CUBE_API_URL 优先于 E2B_API_URL）

SandboxManager 提供 session 级 backend 复用，避免每次工具调用都冷启动 VM。
"""

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
from typing import Protocol, runtime_checkable

import structlog

logger = structlog.get_logger()


@dataclass
class SandboxExecution:
    """执行结果 —— 后端无关的统一形状。"""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    artifacts: list[str] = field(default_factory=list)
    duration_ms: int = 0
    timed_out: bool = False


@runtime_checkable
class SandboxBackend(Protocol):
    """E2B 协议里我们真正需要的子集。上层工具只依赖这几个方法。"""

    sandbox_id: str

    def run_code(self, code: str, language: str = "python", timeout: int = 60) -> SandboxExecution: ...
    def write_file(self, path: str, data: bytes) -> None: ...
    def read_file(self, path: str) -> bytes: ...
    def kill(self) -> None: ...


class MockSandbox:
    """本地 subprocess 假沙箱 —— 开发和 CI 用，**不是** 生产隔离。

    - 每个实例分配独立 tempdir 当工作目录
    - run_code 前后扫描 workdir 找新增文件当 artifacts
    - Python 走 sys.executable -c；shell 走 bash -c，Windows 无 bash 兜底 cmd /c
    """

    def __init__(self, sandbox_id: str | None = None):
        self.sandbox_id = sandbox_id or f"mock-{uuid.uuid4().hex[:8]}"
        self._workdir = Path(tempfile.mkdtemp(prefix=f"leasbx-{self.sandbox_id}-"))
        self._known_files: set[str] = set()
        logger.info("mock_sandbox_created", sandbox_id=self.sandbox_id, workdir=str(self._workdir))

    def _snapshot(self) -> set[str]:
        return {str(p) for p in self._workdir.rglob("*") if p.is_file()}

    def run_code(self, code: str, language: str = "python", timeout: int = 60) -> SandboxExecution:
        pre = self._snapshot()
        started = time.time()

        if language == "python":
            argv = [sys.executable, "-c", code]
        elif language in ("shell", "bash"):
            argv = ["bash", "-c", code] if shutil.which("bash") else ["cmd", "/c", code]
        else:
            return SandboxExecution(
                exit_code=2,
                stderr=f"unsupported language: {language}",
                duration_ms=0,
            )

        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        stdout, stderr, exit_code, timed_out = "", "", 0, False
        try:
            proc = subprocess.run(
                argv,
                cwd=self._workdir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=env,
            )
            stdout, stderr, exit_code = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as e:
            # text=True 时 TimeoutExpired.stdout/.stderr 已是 str
            stdout = e.stdout or ""
            stderr = f"(timeout after {timeout}s)\n" + (e.stderr or "")
            exit_code = 124
            timed_out = True
        except FileNotFoundError as e:
            stderr = f"executor not found: {e}"
            exit_code = 127
        except Exception as e:
            stderr = f"unexpected sandbox error: {e}"
            exit_code = 1

        post = self._snapshot()
        new_files = sorted(post - pre - self._known_files)
        self._known_files.update(new_files)

        return SandboxExecution(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            artifacts=new_files,
            duration_ms=int((time.time() - started) * 1000),
            timed_out=timed_out,
        )

    def write_file(self, path: str, data: bytes) -> None:
        if Path(path).is_absolute():
            raise ValueError(f"absolute path not allowed in mock sandbox: {path}")
        p = (self._workdir / path).resolve()
        # 防路径逃逸
        if self._workdir.resolve() not in p.parents:
            raise ValueError(f"path outside sandbox workdir: {path}")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        self._known_files.add(str(p))

    def read_file(self, path: str) -> bytes:
        # 允许绝对路径的前提是 artifacts 返回过的（在 _known_files 里）
        p_abs = Path(path)
        if p_abs.is_absolute():
            if str(p_abs) not in self._known_files:
                raise ValueError(f"absolute path not previously produced by sandbox: {path}")
            return p_abs.read_bytes()
        # 相对路径下必须在 workdir 内
        p = (self._workdir / path).resolve()
        if self._workdir.resolve() not in p.parents:
            raise ValueError(f"path outside sandbox workdir: {path}")
        return p.read_bytes()

    def kill(self) -> None:
        if self._workdir.exists():
            shutil.rmtree(self._workdir, ignore_errors=True)
        logger.info("mock_sandbox_killed", sandbox_id=self.sandbox_id)


class E2BSandbox:
    """真沙箱后端 —— 走 e2b_code_interpreter SDK，兼容 CubeSandbox。

    环境变量优先级：CUBE_* > E2B_*（匹配 CubeSandbox 迁移语义 —— CubeSandbox
    的 SDK 明确 CUBE_API_URL 覆盖 E2B_API_URL）。
    """

    def __init__(self, sandbox_id: str | None = None):
        try:
            from e2b_code_interpreter import Sandbox  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "SANDBOX_BACKEND=e2b 但 e2b-code-interpreter 未安装。"
                "运行 `uv sync` 或将 SANDBOX_BACKEND 切回 mock。"
            ) from e

        api_url = os.getenv("CUBE_API_URL") or os.getenv("E2B_API_URL")
        api_key = os.getenv("CUBE_API_KEY") or os.getenv("E2B_API_KEY")
        template = os.getenv("CUBE_TEMPLATE_ID")

        kwargs: dict = {}
        if api_url:
            kwargs["api_url"] = api_url
        if api_key:
            kwargs["api_key"] = api_key
        if template:
            kwargs["template"] = template

        self._sandbox = Sandbox(**kwargs)
        self.sandbox_id = getattr(self._sandbox, "sandbox_id", None) or sandbox_id or f"e2b-{uuid.uuid4().hex[:8]}"
        logger.info("e2b_sandbox_created", sandbox_id=self.sandbox_id, api_url=api_url)

    def run_code(self, code: str, language: str = "python", timeout: int = 60) -> SandboxExecution:
        started = time.time()
        try:
            execution = self._sandbox.run_code(code, language=language, timeout=timeout)
        except Exception as e:
            return SandboxExecution(
                stderr=f"e2b run_code error: {e}",
                exit_code=1,
                duration_ms=int((time.time() - started) * 1000),
            )

        logs = getattr(execution, "logs", None)
        stdout = "".join(getattr(logs, "stdout", None) or []) if logs else ""
        stderr = "".join(getattr(logs, "stderr", None) or []) if logs else ""

        exit_code = 0
        err = getattr(execution, "error", None)
        if err:
            exit_code = 1
            stderr += f"\n{getattr(err, 'name', '')}: {getattr(err, 'value', '')}\n{getattr(err, 'traceback', '')}"

        # results 里图片/图表 → 落到本地临时目录当 artifact
        artifacts: list[str] = []
        for i, res in enumerate(getattr(execution, "results", []) or []):
            png_b64 = getattr(res, "png", None)
            if png_b64:
                import base64
                path = Path(tempfile.gettempdir()) / f"sbx-{self.sandbox_id}-{i}.png"
                path.write_bytes(base64.b64decode(png_b64))
                artifacts.append(str(path))

        return SandboxExecution(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            artifacts=artifacts,
            duration_ms=int((time.time() - started) * 1000),
        )

    def write_file(self, path: str, data: bytes) -> None:
        self._sandbox.files.write(path, data)

    def read_file(self, path: str) -> bytes:
        return self._sandbox.files.read(path)

    def kill(self) -> None:
        try:
            self._sandbox.kill()
        except Exception as e:
            logger.warning("e2b_sandbox_kill_failed", sandbox_id=self.sandbox_id, error=str(e))


def get_backend(sandbox_id: str | None = None) -> SandboxBackend:
    """按 SANDBOX_BACKEND 环境变量分派后端。默认 mock。"""
    name = os.getenv("SANDBOX_BACKEND", "mock").lower()
    if name == "e2b":
        return E2BSandbox(sandbox_id=sandbox_id)
    if name == "mock":
        return MockSandbox(sandbox_id=sandbox_id)
    raise ValueError(f"unknown SANDBOX_BACKEND={name!r}; expected 'mock' or 'e2b'")


class SandboxManager:
    """Session 级 backend 复用池 —— 避免每次工具调用都冷启 VM（真 E2B ~60ms）。

    LRU 淘汰：满了 kill 最旧的。session 结束时上层可以显式 release。
    """

    def __init__(self, max_size: int = 32):
        self._pool: dict[str, SandboxBackend] = {}
        self._order: list[str] = []
        self._max_size = max_size

    def get_or_create(self, session_id: str) -> SandboxBackend:
        if session_id in self._pool:
            self._order.remove(session_id)
            self._order.append(session_id)
            return self._pool[session_id]

        while len(self._pool) >= self._max_size:
            oldest = self._order.pop(0)
            try:
                self._pool[oldest].kill()
            except Exception:
                logger.warning("sandbox_evict_kill_failed", session=oldest)
            del self._pool[oldest]

        backend = get_backend(sandbox_id=f"sess-{session_id[:16]}")
        self._pool[session_id] = backend
        self._order.append(session_id)
        return backend

    def release(self, session_id: str) -> None:
        if session_id not in self._pool:
            return
        try:
            self._pool[session_id].kill()
        except Exception:
            pass
        del self._pool[session_id]
        self._order.remove(session_id)

    def clear(self) -> None:
        for sid in list(self._pool.keys()):
            self.release(sid)


_manager: SandboxManager | None = None


def get_manager() -> SandboxManager:
    global _manager
    if _manager is None:
        _manager = SandboxManager()
    return _manager
