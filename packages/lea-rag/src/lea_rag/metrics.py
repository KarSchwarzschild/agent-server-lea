"""Prometheus 监控指标 — lea-rag 检索服务"""
import time
from typing import Callable

from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

# HTTP 请求总数
http_requests_total = Counter(
    'lea_rag_http_requests_total',
    'Total number of HTTP requests',
    ['endpoint', 'method', 'status']
)

# HTTP 请求延迟
http_request_duration = Histogram(
    'lea_rag_http_request_duration_seconds',
    'HTTP request duration in seconds',
    ['endpoint', 'method'],
    buckets=[.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5]
)

# KB 检索查询总数
kb_search_total = Counter(
    'lea_rag_kb_search_total',
    'Total number of KB search queries',
    ['kb_id', 'status']  # status: success, error
)

# KB 检索延迟
kb_search_duration = Histogram(
    'lea_rag_kb_search_duration_seconds',
    'KB search duration in seconds',
    ['kb_id'],
    buckets=[.01, .05, .1, .25, .5, 1, 2.5, 5]
)

# 文档解析任务总数
document_parse_total = Counter(
    'lea_rag_document_parse_total',
    'Total number of document parsing tasks',
    ['status']  # status: pending, started, success, failed
)

# 文档解析延迟
document_parse_duration = Histogram(
    'lea_rag_document_parse_duration_seconds',
    'Document parsing duration in seconds',
    buckets=[1, 5, 10, 30, 60, 300]
)

# 向量化处理总数
embedding_total = Counter(
    'lea_rag_embedding_total',
    'Total number of embedding operations',
    ['status']  # status: success, error
)

# 向量化延迟
embedding_duration = Histogram(
    'lea_rag_embedding_duration_seconds',
    'Embedding operation duration in seconds',
    buckets=[.1, .25, .5, 1, 2.5, 5, 10]
)

# 向量库查询总数
vector_db_queries_total = Counter(
    'lea_rag_vector_db_queries_total',
    'Total number of vector database queries',
    ['operation']  # operation: search, insert, delete
)

# 向量库查询延迟
vector_db_query_duration = Histogram(
    'lea_rag_vector_db_query_duration_seconds',
    'Vector database query duration in seconds',
    ['operation'],
    buckets=[.001, .005, .01, .05, .1, .5, 1]
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


def record_kb_search(kb_id: str, status: str, duration: float):
    """记录 KB 检索指标"""
    kb_search_total.labels(kb_id=kb_id, status=status).inc()
    kb_search_duration.labels(kb_id=kb_id).observe(duration)


def record_document_parse(status: str, duration: float = None):
    """记录文档解析指标"""
    document_parse_total.labels(status=status).inc()
    if duration is not None:
        document_parse_duration.observe(duration)


def record_embedding(status: str, duration: float):
    """记录向量化指标"""
    embedding_total.labels(status=status).inc()
    embedding_duration.observe(duration)


def record_vector_db_query(operation: str, duration: float):
    """记录向量库查询指标"""
    vector_db_queries_total.labels(operation=operation).inc()
    vector_db_query_duration.labels(operation=operation).observe(duration)
