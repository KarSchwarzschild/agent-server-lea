"""Prometheus 监控指标 — lea-rag 检索服务"""
import time
from collections.abc import Callable

from fastapi import Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
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

# Celery 队列深度 —— retrieval-worker 唯一的可观测性入口
#
# 为什么重要：文档上传（POST /{kb_id}/upload 等三处）走 celery_app.send_task 投到
# retrieval 队列，解析→分块→向量化→入库全在 retrieval-worker 里跑。那个容器
# healthcheck 是 disable 的、也没有任何指标，所以它挂掉的表现是：上传接口正常返回
# status="processing"，然后文档永远停在 processing，知识库里搜不到内容 —— 而面板
# 全绿。这个 gauge 是目前唯一能看见那件事的东西（队列只涨不消 = worker 没在干活）。
#
# ⚠️ 这是在 **API 进程**里量 broker，不是在 worker 里埋点。好处是 worker 死了这个数
# 照样准（反而正是它该报警的时候）；代价是**多副本时每个副本都会报同一个值**。
# 所以告警表达式必须用 max()，用 sum() 会按副本数翻倍。
celery_queue_depth = Gauge(
    'lea_rag_celery_queue_depth',
    'Number of pending tasks in the Celery queue (measured on the Redis broker)',
    ['queue']
)


def _endpoint_label(request: Request) -> str:
    """endpoint 标签取**路由模板**而不是实际路径。

    原来用的是 request.url.path，于是 /memories/50、/memories/51 … 各自是一条独立的
    时间序列。路径里带的是用户生成的主键，所以 series 数量没有上限 —— 这是 Prometheus
    最典型的基数爆炸（内存按 series 数涨），而且把资源 ID 泄进了指标。

    改成 /memories/{memory_id} 这种模板之后，一个端点就是一条序列。

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


# ── Celery 队列深度采样 ─────────────────────────────────────────────────────

# Celery 的 Redis broker 把每个队列存成一个 **Redis list**，key 就是队列名，
# 所以 LLEN retrieval 就是待处理任务数。
#
# 开了优先级之后一个逻辑队列会被 kombu 拆成多个 key：retrieval、
# retrieval\x06\x163、retrieval\x06\x166、retrieval\x06\x169（默认
# priority_steps=[0,3,6,9]，sep="\x06\x16"）。本项目**现在没开**优先级
# （tasks.py 里没有 broker_transport_options），所以只有基础 key 有数。
#
# 但这里仍然把四个 key 都加起来：LLEN 打在不存在的 key 上返回 0，代价为零，
# 而将来谁加了优先级配置，这个 gauge 不会静默少报一部分积压。
_PRIORITY_SEP = "\x06\x16"
_PRIORITY_STEPS = (0, 3, 6, 9)


def _queue_keys(queue: str) -> list[str]:
    """一个逻辑队列对应的全部 Redis key（基础 key + 各优先级分片）。"""
    keys = [queue]
    keys.extend(f"{queue}{_PRIORITY_SEP}{step}" for step in _PRIORITY_STEPS if step)
    return keys


async def sample_celery_queue_depth(queue: str = "retrieval") -> int | None:
    """量一次队列深度并写进 gauge。Redis 不可用时返回 None 且不动 gauge。

    返回 None 而不是 0 是刻意的：0 的含义是"队列空了"，拿它表示"没量到"会让
    worker 挂掉和队列正常清空长得一模一样。
    """
    from lea_rag.services.cache import get_redis

    client = get_redis()
    if client is None:
        return None
    try:
        total = 0
        for key in _queue_keys(queue):
            total += await client.llen(key)
    except Exception:
        # broker 读不到不该影响主链路，也不要把上一次的值留在 gauge 里假装还准。
        return None
    celery_queue_depth.labels(queue=queue).set(total)
    return total
