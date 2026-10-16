"""执行路由 — 按 execution_type 分发（连接池复用）。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from lea_authkit import internal_headers

from lea_tools.config import get_internal_service_settings


@dataclass
class ToolCall:
    tool_name: str
    params: dict[str, Any]
    session_id: str = ""
    execution_type: str = ""
    endpoint: str | None = None
    http_adapter_config: dict | None = None
    mcp_server_id: int | None = None
    sandbox_config: dict | None = None


@dataclass
class ToolResult:
    status: str = "success"
    output: Any = None
    error: str | None = None
    usage: dict = field(default_factory=dict)


_http_pool: httpx.AsyncClient | None = None


def _get_http_pool() -> httpx.AsyncClient:
    global _http_pool
    if _http_pool is None:
        _http_pool = httpx.AsyncClient(
            timeout=30,
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        )
    return _http_pool


# internal_headers() 只在 _execute_internal 里用 —— 那是唯一一条打我们自己服务的路。
#
# ⚠️ 别为了省事挂到上面那个 pool 上：同一个 client 还被 _execute_sandbox（打 Cube）
# 和 _execute_http_adapter（打**工具配置里的任意 URL**）共用，挂上去等于把集群密钥
# 贴到每一个外部 endpoint 上。理由和取舍见 lea_authkit/internal_auth.py。


async def execute(call: ToolCall, credential: dict | None) -> ToolResult:
    """四种执行类型的分发口 —— 也是工具执行指标的埋点位置。

    埋在这里而不是 api/execute.py 的路由里：这是唯一的分发口，所有执行类型都从
    这里过，而路由只是其中一个调用方。

    ⚠️ **必须读 result.status，不能只靠 try/except**。下面四个 _execute_* 函数
    失败时返回的是 ToolResult(status="error", ...)，**不抛异常** —— 只包
    try/except 的话所有失败都会被记成 success，指标全绿而工具链其实在报错。
    ToolExecutionHighErrorRate 那条告警就永远不会响。
    """
    from lea_tools.metrics import record_tool_execution

    started = time.perf_counter()
    try:
        match call.execution_type:
            case "api":
                result = await _execute_http_adapter(call, credential)
            case "internal":
                result = await _execute_internal(call, credential)
            case "sandbox":
                result = await _execute_sandbox(call, credential)
            case "mcp":
                result = await _execute_mcp(call, credential)
            case _:
                result = ToolResult(
                    status="error", error=f"unknown type: {call.execution_type}"
                )
    except Exception:
        # 处理函数自己兜不住的异常（比如适配器里的编程错误）。这条路上不返回
        # ToolResult，所以上面那句 result.status 读不到，得单独记一笔。
        record_tool_execution(call.tool_name, "error", time.perf_counter() - started)
        raise

    # status 的取值由 ToolResult 决定（success / error，超时也归 error）。
    # tool_name 取 call.tool_name —— 那是库里的裸名（管理员注册的），有界；
    # **不是** req.tool_name（模型发来的暴露名，带 my__ 前缀的用户私有工具）。
    record_tool_execution(
        call.tool_name, result.status or "success", time.perf_counter() - started
    )
    return result


INTERNAL_ROUTES: dict[str, str] = {
    "kb_search": "/kb/search",
    "memory_search": "/memory/search",
    "memory_add": "/memory/add",
}


async def _execute_internal(call: ToolCall, credential: dict | None) -> ToolResult:
    settings = get_internal_service_settings()
    path = INTERNAL_ROUTES.get(call.tool_name)
    if not path:
        path = call.endpoint or f"/{call.tool_name}"
    url = f"{settings.lea_rag_url}{path}"

    client = _get_http_pool()
    try:
        resp = await client.post(url, json=call.params, headers=internal_headers())
        if resp.status_code >= 400:
            return ToolResult(status="error", error=resp.text)
        return ToolResult(output=resp.json())
    except httpx.ConnectError as e:
        return ToolResult(status="error", error=f"service unreachable: {e}")


async def _execute_http_adapter(call: ToolCall, credential: dict | None) -> ToolResult:
    config = call.http_adapter_config
    if not config:
        return ToolResult(status="error", error="missing http_adapter_config")

    from lea_tools.utils.template import render_template

    endpoint = config["endpoint"]
    method = config.get("method", "POST").upper()
    headers = render_template(config.get("headers_template", {}), credential or {})
    body = render_template(config.get("request_mapping", {}).get("body", {}), call.params)

    client = _get_http_pool()
    try:
        if method == "GET":
            resp = await client.get(endpoint, headers=headers, params=body)
        else:
            resp = await client.request(method, endpoint, headers=headers, json=body)
    except httpx.ConnectError as e:
        return ToolResult(status="error", error=f"endpoint unreachable: {e}")

    if resp.status_code >= 400:
        return ToolResult(status="error", error=resp.text)

    result = resp.json()
    response_mapping = config.get("response_mapping", {}).get("result")
    if response_mapping:
        from jsonpath_ng import parse as jp_parse
        expr = jp_parse(response_mapping)
        matches = expr.find(result)
        result = [m.value for m in matches] if matches else result

    return ToolResult(output=result)


async def _execute_sandbox(call: ToolCall, credential: dict | None) -> ToolResult:
    """沙箱执行 — 优先复用 session（有状态），否则 fallback 无状态执行。

    Session 模式：params.session_id 存在时，通过 SandboxSessionManager 复用同一 microVM，
    支持多轮 code_interpreter 共享文件系统和 Python 上下文。
    """
    session_id = call.params.get("session_id") or call.session_id
    code = call.params.get("code", "")
    language = call.params.get("language", "python")
    timeout = int(call.params.get("timeout", 60))

    if session_id:
        from lea_tools.services.sandbox_session import get_sandbox_manager
        mgr = get_sandbox_manager()

        if not mgr.get(session_id):
            await mgr.create(session_id=session_id, user_id="", tenant_id="")

        try:
            data = await mgr.execute(session_id, code, language, timeout)
        except Exception as e:
            return ToolResult(status="error", error=f"sandbox exec failed: {e}")

        return ToolResult(
            output={
                "stdout": data.get("stdout", ""),
                "stderr": data.get("stderr", ""),
                "exit_code": data.get("exit_code", 0),
                "artifacts": data.get("artifacts", []),
                "duration_ms": data.get("duration_ms", 0),
            },
            usage={"credits": 2},
        )

    settings = get_internal_service_settings()
    client = _get_http_pool()
    sandbox_cfg = call.sandbox_config or {}
    template = sandbox_cfg.get("template", "default")
    timeout_ms = sandbox_cfg.get("timeout", 30000)

    try:
        resp = await client.post(
            f"{settings.e2b_api_url}/api/execute",
            json={"code": code, "language": language,
                  "template": template, "timeout": timeout_ms},
            timeout=timeout_ms / 1000 + 5,
        )
    except httpx.ConnectError as e:
        return ToolResult(status="error", error=f"sandbox unreachable: {e}")

    if resp.status_code >= 400:
        return ToolResult(status="error", error=resp.text)

    data = resp.json()
    return ToolResult(
        output={"stdout": data.get("stdout", ""), "stderr": data.get("stderr", ""),
                "artifacts": data.get("artifacts", [])},
        usage={"credits": 2},
    )


async def _execute_mcp(call: ToolCall, credential: dict | None) -> ToolResult:
    from lea_tools.executor.mcp_proxy import mcp_call
    return await mcp_call(call, credential)
