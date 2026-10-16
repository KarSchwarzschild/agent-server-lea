"""Prometheus 监控指标 — lea-tools 工具网关"""
import time
from collections.abc import Callable

from fastapi import Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware

# HTTP 请求总数
http_requests_total = Counter(
    'lea_tools_http_requests_total',
    'Total number of HTTP requests',
    ['endpoint', 'method', 'status']
)

# HTTP 请求延迟
http_request_duration = Histogram(
    'lea_tools_http_request_duration_seconds',
    'HTTP request duration in seconds',
    ['endpoint', 'method'],
    buckets=[.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5]
)

# 工具执行总数
tool_executions_total = Counter(
    'lea_tools_tool_executions_total',
    'Total number of tool executions',
    ['tool_name', 'status']  # status: success, error, timeout
)

# 工具执行延迟
tool_execution_duration = Histogram(
    'lea_tools_tool_execution_duration_seconds',
    'Tool execution duration in seconds',
    ['tool_name'],
    buckets=[.01, .05, .1, .5, 1, 2, 5, 10, 30]
)

# MCP 服务调用总数
#
# status 的五个取值把"谁的问题"分开了，混成一个 error 的话排查方向全丢：
#   success      远端正常返回
#   error        MCPError —— 远端活着但协议层报错
#   unreachable  连不上（服务挂了 / 网络不通）
#   http_error   连上了但返回 4xx/5xx
#   bad_request  调用方参数缺失，**不是远端的问题** —— 不单独记会把"MCP 不可用"
#                的错误率推高，而远端其实好得很
#
# 只覆盖真正发出去的远端 RPC。配置错误（没有 mcp_server_id、server 停用、
# transport 不支持、SSRF 拦截）在 RPC 之前就返回了，不记进来 —— 那些不是
# "MCP 服务挂了"，混进去会让错误率失真。
#
# server_name 是管理员注册的，取值有界。
mcp_calls_total = Counter(
    'lea_tools_mcp_calls_total',
    'Total number of MCP calls',
    ['server_name', 'status']
)

# MCP 服务调用延迟
mcp_call_duration = Histogram(
    'lea_tools_mcp_call_duration_seconds',
    'MCP call duration in seconds',
    ['server_name'],
    buckets=[.01, .05, .1, .5, 1, 2, 5]
)

# 沙箱执行总数
#
# ⚠️ language **必须先过 normalize_language()**。它来自请求体（ExecRequest.language），
# 是外部可控的字符串 —— 直接当标签就是一条远程可触发的无界序列增长路径：调用方
# 每发一个没见过的 language 值就多一条序列，而两个后端其实只认 python 和
# bash/shell/sh（其余一律返回 "unsupported language"）。
#
# 原来的注释写 "language: python, nodejs, etc."，那个 etc. 正是问题所在：nodejs
# 后端压根不支持，而真实取值的集合是开放的。
#
# status: success / error / timeout
sandbox_executions_total = Counter(
    'lea_tools_sandbox_executions_total',
    'Total number of sandbox executions',
    ['language', 'status']
)

# 沙箱执行延迟
sandbox_execution_duration = Histogram(
    'lea_tools_sandbox_execution_duration_seconds',
    'Sandbox execution duration in seconds',
    ['language'],
    buckets=[.1, .5, 1, 2, 5, 10, 30, 60]
)

# 文件操作总数
#
# operation 只有 upload / download 两个（沙箱的文件进出口）。原来注释里写的
# read / write 没有对应的代码路径 —— 照注释去接埋点会接出两个永远没数的标签值。
#
# status: success / unavailable（沙箱后端连不上）/ no_session（会话被回收了）
#         / not_found（仅 download：要的路径不存在）
#
# not_found 和 no_session 分开的理由：模型猜文件名猜错很常见，而"沙箱过期了"是
# 另一类问题。混成一个的话，后者会被日常的找不到文件彻底淹掉。
file_operations_total = Counter(
    'lea_tools_file_operations_total',
    'Total number of file operations',
    ['operation', 'status']
)

# 文件操作延迟
file_operation_duration = Histogram(
    'lea_tools_file_operation_duration_seconds',
    'File operation duration in seconds',
    ['operation'],
    buckets=[.01, .05, .1, .5, 1, 5, 10]
)


def _endpoint_label(request: Request) -> str:
    """endpoint 标签取**路由模板**而不是实际路径。

    原来这里是 request.url.path，而 lea-tools 有一批带路径参数的路由：

        POST   /sandbox/sessions/{session_id}/exec
        POST   /sandbox/sessions/{session_id}/upload
        GET    /sandbox/sessions/{session_id}/download
        GET    /sandbox/sessions/{session_id}
        DELETE /sandbox/sessions/{session_id}
        POST   /admin/mcp/{server_id}/test、/sync

    session_id 是 uuid4().hex[:12]（services/sandbox_session.py:177），每开一个
    沙箱就是一个新值 —— 于是每次 exec/upload/download 都产生一条**新的时间序列**，
    数量没有上限。Prometheus 的内存按序列数涨，这是一条无界增长路径，而且把
    会话 ID 泄进了指标。

    改成模板之后一个端点就是一条序列。

    拿不到 route（404 没匹配任何路由，或中间件在路由解析前短路）归到固定的
    "unmatched"。**不能回退到 request.url.path**：404 路径是外部可控的，扫描器
    打一串随机路径就能把序列数顶上去，等于留了个远程打爆监控的口子。

    另外四个服务（lea-config / lea-rag / lea-agent / lea-gateway）都已经是这个
    写法，lea-tools 是最后一个。
    """
    route = request.scope.get("route")
    path_format = getattr(route, "path", None) if route else None
    return path_format or "unmatched"


class MetricsMiddleware(BaseHTTPMiddleware):
    """记录 HTTP 请求指标的中间件"""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start = time.time()

        # 执行请求
        response = await call_next(request)

        # 记录指标
        duration = time.time() - start
        endpoint = _endpoint_label(request)
        method = request.method
        status = self._status_to_label(response.status_code)

        http_requests_total.labels(
            endpoint=endpoint,
            method=method,
            status=status
        ).inc()

        http_request_duration.labels(
            endpoint=endpoint,
            method=method
        ).observe(duration)

        return response

    @staticmethod
    def _status_to_label(code: int) -> str:
        if 200 <= code < 300:
            return '2xx'
        elif 300 <= code < 400:
            return '3xx'
        elif 400 <= code < 500:
            return '4xx'
        elif code >= 500:
            return '5xx'
        return 'unknown'


def metrics_endpoint() -> Response:
    """Prometheus 指标端点"""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )


def record_tool_execution(tool_name: str, status: str, duration: float):
    """记录工具执行指标"""
    tool_executions_total.labels(tool_name=tool_name, status=status).inc()
    tool_execution_duration.labels(tool_name=tool_name).observe(duration)


def record_mcp_call(server_name: str, status: str, duration: float):
    """记录 MCP 服务调用指标"""
    mcp_calls_total.labels(server_name=server_name, status=status).inc()
    mcp_call_duration.labels(server_name=server_name).observe(duration)


# 两个沙箱后端认的语言（_cube_sandbox.py:48 的 _SHELL_LANGUAGES + python）。
# 其余一律返回 "unsupported language"，所以归到一个固定的 other。
_SANDBOX_LANGUAGES = frozenset({"python", "bash", "shell", "sh"})


def normalize_language(language: str | None) -> str:
    """把用户传来的 language 收敛到有界集合。

    ExecRequest.language 是请求体字段，外部可控。不收敛就是一条远程可触发的
    序列增长路径 —— 调用方每发一个新值就多一条序列（含直方图是 9 条）。

    大小写也归一：Python / PYTHON / python 是同一件事，分成三条序列没有意义。
    """
    lang = (language or "python").strip().lower()
    return lang if lang in _SANDBOX_LANGUAGES else "other"


def record_sandbox_execution(language: str, status: str, duration: float):
    """记录沙箱执行指标。调用点：api/sandbox.py 的 exec_code。

    language 在这里再过一次 normalize —— 调用方忘了归一也不会漏出无界标签。
    """
    lang = normalize_language(language)
    sandbox_executions_total.labels(language=lang, status=status).inc()
    sandbox_execution_duration.labels(language=lang).observe(duration)


def record_file_operation(operation: str, status: str, duration: float):
    """记录文件操作指标"""
    file_operations_total.labels(operation=operation, status=status).inc()
    file_operation_duration.labels(operation=operation).observe(duration)
