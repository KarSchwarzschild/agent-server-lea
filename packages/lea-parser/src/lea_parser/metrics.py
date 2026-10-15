"""Prometheus 监控指标 — lea-parser 文件处理服务。

这个模块里**每一个指标都有调用点**。仓库里另外三个服务的 metrics.py 各有一批
record_* 函数只有定义、零调用（2026-10-15 查出来共 10 个），后果是指标在
/metrics 上只有 HELP/TYPE 两行、引用它的告警永远不触发。那笔债不在这里再添。

所以本文件只定义两类：
  HTTP 中间件     —— 挂上就自动记，不需要业务代码配合
  解析/配额指标    —— 同一个提交里接进 api/parse.py，接完才算
"""
import time
from collections.abc import Callable

from fastapi import Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware

http_requests_total = Counter(
    'lea_parser_http_requests_total',
    'Total number of HTTP requests',
    ['endpoint', 'method', 'status']
)

http_request_duration = Histogram(
    'lea_parser_http_request_duration_seconds',
    'HTTP request duration in seconds',
    ['endpoint', 'method'],
    buckets=[.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10, 30]
)

# 解析任务。format 标签取自 SUPPORTED_FORMATS（有界的常量集合），status 两个取值。
# 不加 tenant_id / user_id —— 那是无界的，会把序列数顶爆，而且把租户标识泄进指标。
parse_total = Counter(
    'lea_parser_parse_total',
    'Total number of document parse operations',
    ['format', 'status']  # status: success, error
)

# 解析耗时。bucket 上限给到 300s：真实文件解析（扫描件走 pymupdf 兜底、大 pptx）
# 是几十秒量级的，按 HTTP 那套 5s 封顶会让所有真实解析都挤在 +Inf 里，分位数失真。
parse_duration = Histogram(
    'lea_parser_parse_duration_seconds',
    'Document parse duration in seconds',
    ['format'],
    buckets=[.5, 1, 2.5, 5, 10, 30, 60, 120, 300]
)

# 配额拒绝。reason 只有两个取值，对应 parse.py 里两个 QuotaError 分支。
quota_rejections_total = Counter(
    'lea_parser_quota_rejections_total',
    'Total number of requests rejected by quota checks',
    ['reason']  # reason: rate_limit, file_size
)


def _endpoint_label(request: Request) -> str:
    """endpoint 标签取**路由模板**而不是实际路径。

    用 request.url.path 的话，/usage/{task_id} 这种带主键的路径每个 id 一条独立
    序列，序列数没有上限 —— Prometheus 的内存按序列数涨，而且把资源 ID 泄进了
    指标。

    拿不到 route 时（404 没匹配任何路由，或中间件在路由解析前就短路）归到一个
    固定的 "unmatched"。**不能回退到 request.url.path**：404 的路径是外部可控的，
    扫描器打一串随机路径就能把序列数顶上去，等于留了个远程打爆监控的口子。
    """
    route = request.scope.get("route")
    path_format = getattr(route, "path", None) if route else None
    return path_format or "unmatched"


class MetricsMiddleware(BaseHTTPMiddleware):
    """记录 HTTP 请求指标。"""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start = time.time()
        response = await call_next(request)
        duration = time.time() - start

        endpoint = _endpoint_label(request)
        http_requests_total.labels(
            endpoint=endpoint,
            method=request.method,
            status=self._status_to_label(response.status_code),
        ).inc()
        http_request_duration.labels(
            endpoint=endpoint, method=request.method
        ).observe(duration)
        return response

    @staticmethod
    def _status_to_label(code: int) -> str:
        if 200 <= code < 300:
            return '2xx'
        if 300 <= code < 400:
            return '3xx'
        if 400 <= code < 500:
            return '4xx'
        if code >= 500:
            return '5xx'
        return 'unknown'


def metrics_endpoint() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


def record_parse(fmt: str, status: str, duration: float) -> None:
    """记录一次解析。调用点：api/parse.py 的 parse_file。"""
    parse_total.labels(format=fmt or "unknown", status=status).inc()
    parse_duration.labels(format=fmt or "unknown").observe(duration)


def record_quota_rejection(reason: str) -> None:
    """记录一次配额拒绝。调用点：api/parse.py 的两个 QuotaError 分支。"""
    quota_rejections_total.labels(reason=reason).inc()
