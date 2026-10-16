"""Prometheus 监控指标"""
import time
from collections.abc import Callable

from fastapi import Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
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
#
# status 的真实取值是 cache_hit / cache_miss，不是原来注释里写的
# success / error / cache_hit。改这行注释的理由：CacheInvalidationRateHigh 这条
# 告警算的是 status="cache_miss" 占全部的比例，而 cache_miss 从来不在注释列出的
# 取值里 —— 照注释去接埋点的人会接出一个永远不触发的告警。
#
# 调用点在 api/agents.py 的 list_agents（agent 列表接口）。注意**不是**
# api/internal.py 的 get_agent_config：那个接口压根没有缓存，接在那里的话
# cache_miss 永远产不出来，这条告警接了也还是哑炮。
agent_config_loads_total = Counter(
    'lea_config_agent_config_loads_total',
    'Total number of agent config loads',
    ['status']  # cache_hit, cache_miss
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


# ── DB 指标的自动安装 ────────────────────────────────────────────────────────
#
# 为什么挂引擎事件而不是在每个查询点手动调 record_db_query：手动调意味着将来每加
# 一处查询都要记得补一行，忘了就是一个新的覆盖缺口，而且缺口不会报错。这个仓库
# 已经因为"写了 helper 但没人调"攒了 10 个死埋点，不能再靠自觉。
#
# 挂在引擎上之后，覆盖范围是**所有**走这个 engine 的 SQL，包括将来新加的。

_MAX_TABLES = 64

# operation 标签只认这几个动词，其余归 other —— 有界。
_KNOWN_OPS = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"})

# table 标签的白名单在首次见到时建立，上限 _MAX_TABLES。
# 为什么要有上限：表名是从 SQL 文本里正则抠出来的，万一抠错（比如抠到了一个
# 字面量或者子查询别名），没有上限就是一条无界的序列增长路径。超限后统一归
# other，指标失真但不会把 Prometheus 的内存吃掉。
_seen_tables: set[str] = set()


def _parse_sql(sql: str) -> tuple[str, str]:
    """从 SQL 文本里取 (operation, table)，两个都收敛到有界集合。"""
    stripped = sql.lstrip()
    verb = stripped[:6].upper().strip()
    op = verb if verb in _KNOWN_OPS else "other"

    table = "other"
    if op != "other":
        # SELECT ... FROM x / INSERT INTO x / UPDATE x / DELETE FROM x
        lowered = stripped.lower()
        for marker in (" from ", " into ", "update "):
            idx = lowered.find(marker)
            if idx < 0:
                continue
            rest = stripped[idx + len(marker):].lstrip()
            name = rest.split()[0] if rest.split() else ""
            name = name.strip('`"\'();,').split(".")[-1]
            if name and name.replace("_", "").isalnum():
                if name in _seen_tables or len(_seen_tables) < _MAX_TABLES:
                    _seen_tables.add(name)
                    table = name
                break
    return op, table


def install_db_metrics(engine) -> None:
    """给引擎挂上查询计时。幂等 —— 重复调用只装一次。"""
    from sqlalchemy import event

    if getattr(engine, "_lea_metrics_installed", False):
        return

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):
        conn.info.setdefault("_lea_q_start", []).append(time.perf_counter())

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):
        stack = conn.info.get("_lea_q_start")
        if not stack:
            return
        duration = time.perf_counter() - stack.pop()
        op, table = _parse_sql(statement)
        record_db_query(op, table, duration)

    @event.listens_for(engine, "handle_error")
    def _on_error(context):
        # 查询出错时 after_cursor_execute **不会触发**。不在这里把栈弹掉的话，
        # 每个失败查询都往 conn.info 里留一个时间戳；连接是池化复用的（这个引擎
        # 配了 pool_recycle=3600，一条连接能活一小时），于是这个列表只增不减。
        #
        # 后果**只是内存泄漏，不是耗时算错**：栈是 LIFO，每个查询弹的是自己压进
        # 去的那个，泄漏的时间戳沉在栈底不会被后续查询碰到。一开始我以为会算错，
        # 写了条"失败后耗时被算成天文数字"的测试，注入缺陷后它照样通过 —— 那条
        # 测试已经删掉了，留着就是个永远不会红的装饰。
        #
        # 真实风险是一个持续报错的查询路径（比如某张表被删了但代码还在查）把
        # 长寿连接的 info 列表撑大。单条记录很小，但这种泄漏不会自愈。
        conn = getattr(context, "connection", None)
        stack = conn.info.get("_lea_q_start") if conn is not None else None
        if stack:
            stack.pop()

    engine._lea_metrics_installed = True


def record_agent_config_load(status: str, duration: float):
    """记录 Agent 配置加载指标"""
    agent_config_loads_total.labels(status=status).inc()
    agent_config_load_duration.observe(duration)
