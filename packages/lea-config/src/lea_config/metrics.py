"""Prometheus 监控指标"""
import time
from typing import Callable

from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

# HTTP 请求总数
http_requests_total = Counter(
    'lea_config_http_requests_total',
    'Total number of HTTP requests',
    ['endpoint', 'method', 'status']
)

# HTTP 请求延迟
http_request_duration = Histogram(
    'lea_config_http_request_duration_seconds',
    'HTTP request duration in seconds',
    ['endpoint', 'method'],
    buckets=[.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5]
)

# 数据库查询总数
db_queries_total = Counter(
    'lea_config_db_queries_total',
    'Total number of database queries',
    ['operation', 'table']
)

# 数据库查询延迟
db_query_duration = Histogram(
    'lea_config_db_query_duration_seconds',
    'Database query duration in seconds',
    ['operation', 'table'],
    buckets=[.001, .005, .01, .025, .05, .1, .5, 1]
)

# Agent 配置加载次数
agent_config_loads_total = Counter(
    'lea_config_agent_config_loads_total',
    'Total number of agent config loads',
    ['status']  # success, error, cache_hit
)

# Agent 配置加载延迟
agent_config_load_duration = Histogram(
    'lea_config_agent_config_load_duration_seconds',
    'Agent config load duration in seconds',
    buckets=[.001, .005, .01, .05, .1, .5, 1]
)


def _endpoint_label(request: Request) -> str:
    """endpoint 标签取**路由模板**而不是实际路径。

    原来用的是 request.url.path，于是 /agents/1、/agents/2 … 各自是一条独立的时间
    序列。路径里带的是用户生成的主键，所以 series 数量没有上限 —— 这是 Prometheus
    最典型的基数爆炸（内存按 series 数涨），而且把资源 ID 泄进了指标。

    改成 /agents/{agent_id} 这种模板之后，一个端点就是一条序列。

    拿不到 route 的情况（404 没匹配上任何路由、或中间件在路由之前就短路了）归成
    一个固定的 "unmatched" —— 这里**不能回退到 request.url.path**，否则扫描器打一
    串随机路径就能把序列数顶上去，等于留了个远程把监控打爆的口子。
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


def record_db_query(operation: str, table: str, duration: float):
    """记录数据库查询指标"""
    db_queries_total.labels(operation=operation, table=table).inc()
    db_query_duration.labels(operation=operation, table=table).observe(duration)


def record_agent_config_load(status: str, duration: float):
    """记录 Agent 配置加载指标"""
    agent_config_loads_total.labels(status=status).inc()
    agent_config_load_duration.observe(duration)
