"""CubeSandbox SDK 命令执行适配测试。"""

from __future__ import annotations

import sys
import types

import pytest


class _Result:
    def __init__(self, stdout="", stderr="", exit_code=0):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code


class _Logs:
    stdout = ["python output\n"]
    stderr = []


class _PythonResult:
    logs = _Logs()
    error = None


class _Commands:
    def __init__(self):
        self.calls = []

    def run(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return _Result("shell output\n", "shell error\n", 7)


class _Sandbox:
    created = []
    connected = []

    def __init__(self, sandbox_id="sbx-new"):
        self.sandbox_id = sandbox_id
        self.domain = "cube.app"
        self.commands = _Commands()
        self.files = types.SimpleNamespace(
            write=lambda *_: None,
            read=lambda *_: b"file",
        )
        self.python_calls = []

    @classmethod
    def create(cls, **kwargs):
        cls.created.append(kwargs)
        return cls()

    @classmethod
    def connect(cls, sandbox_id, **kwargs):
        cls.connected.append((sandbox_id, kwargs))
        return cls(sandbox_id)

    def run_code(self, code, **kwargs):
        self.python_calls.append((code, kwargs))
        return _PythonResult()

    def kill(self):
        pass


class _Config:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


@pytest.fixture
def cube_module(monkeypatch):
    _Sandbox.created.clear()
    _Sandbox.connected.clear()
    monkeypatch.setitem(sys.modules, "cubesandbox", types.SimpleNamespace(
        Config=_Config, Sandbox=_Sandbox,
    ))
    monkeypatch.setattr(
        "lea_tools.services._cube_sandbox.ensure_session_dir",
        lambda *_: object(),
    )


def test_shell_uses_native_commands_api(cube_module):
    from lea_tools.services._cube_sandbox import CubeSandbox

    sandbox = CubeSandbox(session_id="session-1", tenant_id="tenant-1")
    result = sandbox.run_code("mkdir -p output && echo ok > output/a.txt", "bash", 45)

    command, kwargs = sandbox._sdk.commands.calls[0]
    assert command == "mkdir -p output && echo ok > output/a.txt"
    assert kwargs == {"cwd": "/workspace", "timeout": 45, "user": "root"}
    assert (result.stdout, result.stderr, result.exit_code) == (
        "shell output\n", "shell error\n", 7,
    )
    mounts = _Sandbox.created[0]["metadata"]["host-mount"]
    assert '"mountPath": "/workspace"' in mounts


def test_python_still_uses_code_kernel(cube_module):
    from lea_tools.services._cube_sandbox import CubeSandbox

    sandbox = CubeSandbox(session_id="session-1", tenant_id="tenant-1")
    result = sandbox.run_code("print('ok')", "python", 30)

    code, kwargs = sandbox._sdk.python_calls[0]
    assert "os.chdir('/workspace')" in code
    assert code.endswith("print('ok')")
    assert kwargs == {"timeout": 30}
    assert result.stdout == "python output\n"


def test_existing_sandbox_id_is_connected(cube_module):
    from lea_tools.services._cube_sandbox import CubeSandbox

    sandbox = CubeSandbox(session_id="session-1", tenant_id="tenant-1", sandbox_id="sbx-existing")

    assert sandbox.sandbox_id == "sbx-existing"
    assert _Sandbox.connected[0][0] == "sbx-existing"
    assert _Sandbox.created == []


def test_new_sandbox_requires_nfs_host_mount(cube_module, monkeypatch):
    from lea_tools.services._cube_sandbox import CubeSandbox

    monkeypatch.setattr(
        "lea_tools.services._cube_sandbox.ensure_session_dir",
        lambda *_: None,
    )

    with pytest.raises(RuntimeError, match="NFS workspace is unavailable"):
        CubeSandbox(session_id="session-1", tenant_id="tenant-1")

    assert _Sandbox.created == []
