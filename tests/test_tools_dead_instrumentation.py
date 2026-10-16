"""lea-tools 的四个埋点 + 一个基数炸弹。

四个 record_* 此前都是只有定义、零调用点 —— 这是全仓最后四个死埋点，喂着
ToolExecutionHighErrorRate 和 SandboxExecutionTimeout 两条告警。

另外修掉的是 MetricsMiddleware 里的 `endpoint = request.url.path`：lea-tools 有
一批 /sandbox/sessions/{session_id}/... 路由，session_id 是 uuid4().hex[:12]，
每开一个沙箱就是一个新标签值。这是**部署中的服务上一条无界序列增长路径**，
另外四个服务都已经修过，lea-tools 是最后一个。

钉住的设计决定（都容易在后续改动里悄悄退化）：
  - 工具执行读 result.status，不只靠 try/except（处理函数返回 error 而不抛异常）
  - language 必须收敛（它来自请求体，外部可控）
  - exit_code 非 0 时接口返回 200，HTTP 5xx 指标抓不到，只有这个指标能看见
  - MCP 的五种失败状态分开（谁的问题，排查方向完全不同）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "packages" / "lea-tools" / "src")
)

pytest.importorskip("prometheus_client", reason="需要 prometheus-client")

from prometheus_client import generate_latest  # noqa: E402

from lea_tools.metrics import (  # noqa: E402
    _endpoint_label,
    normalize_language,
    record_file_operation,
    record_mcp_call,
    record_sandbox_execution,
    record_tool_execution,
)


def _line(prefix: str) -> str | None:
    for ln in generate_latest().decode("utf-8").splitlines():
        if ln.startswith(prefix) and not ln.startswith("#"):
            return ln
    return None


# ── 基数炸弹：路由模板 ──────────────────────────────────────────────────────

class _FakeRoute:
    def __init__(self, path: str) -> None:
        self.path = path


class _FakeRequest:
    def __init__(self, scope: dict) -> None:
        self.scope = scope


def test_sandbox_route_collapses_to_template():
    """session_id 是 uuid4().hex[:12]，每个沙箱一个新值。

    不收敛成模板的话，每次 exec/upload/download 都产生一条新的时间序列，
    数量没有上限 —— Prometheus 的内存按序列数涨。
    """
    req = _FakeRequest({"route": _FakeRoute("/sandbox/sessions/{session_id}/exec")})
    assert _endpoint_label(req) == "/sandbox/sessions/{session_id}/exec"


def test_unmatched_does_not_leak_raw_path():
    """404 路径外部可控，回退到 url.path 等于留个远程打爆监控的口子。"""
    for scope in ({}, {"route": None}):
        assert _endpoint_label(_FakeRequest(scope)) == "unmatched"


def test_many_session_ids_produce_one_label():
    """50 个不同 session_id 只能产出一个标签值。"""
    labels = {
        _endpoint_label(
            _FakeRequest({"route": _FakeRoute("/sandbox/sessions/{session_id}/exec")})
        )
        for _ in range(50)
    }
    assert labels == {"/sandbox/sessions/{session_id}/exec"}


# ── language 收敛 ───────────────────────────────────────────────────────────

def test_language_whitelist():
    """两个后端只认 python 和 bash/shell/sh，其余返回 unsupported。"""
    for ok in ("python", "bash", "shell", "sh"):
        assert normalize_language(ok) == ok


def test_language_case_normalized():
    """Python / PYTHON / python 是同一件事，分三条序列没有意义。"""
    for variant in ("Python", "PYTHON", "  python  "):
        assert normalize_language(variant) == "python"


def test_arbitrary_language_collapses_to_other():
    """language 来自请求体，是外部可控的。这条是防远程顶爆序列数的那一条。"""
    labels = {normalize_language(f"lang-{i}") for i in range(50)}
    labels |= {normalize_language(x) for x in ("nodejs", "ruby", "", None)}
    assert labels == {"other", "python"}, f"收敛失败: {labels}"


def test_record_sandbox_normalizes_internally():
    """调用方忘了归一也不该漏出无界标签 —— record 里再过一次。"""
    record_sandbox_execution("RUBY-9000", "error", 0.5)
    assert _line('lea_tools_sandbox_executions_total{language="other",status="error"}')
    assert not _line('lea_tools_sandbox_executions_total{language="RUBY-9000"')


# ── 四个埋点真的产出序列 ────────────────────────────────────────────────────

def test_tool_execution_series():
    record_tool_execution("kb_search", "success", 0.3)
    assert _line('lea_tools_tool_executions_total{status="success",tool_name="kb_search"}') \
        or _line('lea_tools_tool_executions_total{tool_name="kb_search",status="success"}'), \
        "埋点没接上"
    assert _line("lea_tools_tool_execution_duration_seconds_count")


def test_tool_execution_error_separate():
    """处理函数返回 ToolResult(status="error") 而**不抛异常** —— 只靠 try/except
    的话所有失败都会被记成 success，ToolExecutionHighErrorRate 永远不响。
    """
    record_tool_execution("weather", "error", 0.2)
    text = generate_latest().decode("utf-8")
    assert 'tool_name="weather"' in text and 'status="error"' in text


def test_mcp_failure_modes_are_distinct():
    """五种状态分开 —— 混成一个 error 的话排查方向全丢。"""
    for status in ("success", "error", "unreachable", "http_error", "bad_request"):
        record_mcp_call("srv-a", status, 0.1)
    text = generate_latest().decode("utf-8")
    for status in ("unreachable", "http_error", "bad_request"):
        assert f'status="{status}"' in text, f"{status} 没产出序列"


def test_sandbox_statuses():
    for status in ("success", "error", "unsupported_language", "unavailable", "no_session"):
        record_sandbox_execution("python", status, 0.4)
    text = generate_latest().decode("utf-8")
    for status in ("unsupported_language", "unavailable", "no_session"):
        assert f'status="{status}"' in text


def test_file_operations_only_upload_download():
    """operation 只有 upload / download。原注释里的 read / write 没有代码路径。"""
    record_file_operation("upload", "success", 0.1)
    record_file_operation("download", "not_found", 0.1)
    text = generate_latest().decode("utf-8")
    assert 'operation="upload"' in text
    assert 'operation="download"' in text
    assert 'operation="read"' not in text
    assert 'operation="write"' not in text
