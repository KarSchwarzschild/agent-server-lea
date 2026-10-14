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
mcp_calls_total = Counter(
    'lea_tools_mcp_calls_total',
    'Total number of MCP calls',
    ['server_name', 'status']  # status: success, error
)

# MCP 服务调用延迟
mcp_call_duration = Histogram(
    'lea_tools_mcp_call_duration_seconds',
    'MCP call duration in seconds',
    ['server_name'],
    buckets=[.01, .05, .1, .5, 1, 2, 5]
)

# 沙箱执行总数
sandbox_executions_total = Counter(
    'lea_tools_sandbox_executions_total',
    'Total number of sandbox executions',
    ['language', 'status']  # language: python, nodejs, etc.; status: success, error, timeout
)

# 沙箱执行延迟
sandbox_execution_duration = Histogram(
    'lea_tools_sandbox_execution_duration_seconds',
    'Sandbox execution duration in seconds',
    ['language'],
    buckets=[.1, .5, 1, 2, 5, 10, 30, 60]
)

# 文件操作总数
file_operations_total = Counter(
    'lea_tools_file_operations_total',
    'Total number of file operations',
    ['operation', 'status']  # operation: upload, download, read, write; status: success, error
)

# 文件操作延迟
file_operation_duration = Histogram(
    'lea_tools_file_operation_duration_seconds',
    'File operation duration in seconds',
    ['operation'],
    buckets=[.01, .05, .1, .5, 1, 5, 10]
)


class MetricsMiddleware(BaseHTTPMiddleware):
    """记录 HTTP 请求指标的中间件"""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start = time.time()

        # 执行请求
        response = await call_next(request)

        # 记录指标
        duration = time.time() - start
        endpoint = request.url.path
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


def record_sandbox_execution(language: str, status: str, duration: float):
    """记录沙箱执行指标"""
    sandbox_executions_total.labels(language=language, status=status).inc()
    sandbox_execution_duration.labels(language=language).observe(duration)


def record_file_operation(operation: str, status: str, duration: float):
    """记录文件操作指标"""
    file_operations_total.labels(operation=operation, status=status).inc()
    file_operation_duration.labels(operation=operation).observe(duration)
