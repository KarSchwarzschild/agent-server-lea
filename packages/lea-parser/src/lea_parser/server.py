"""LEA Parser Server — 文件处理微服务入口。

启动：uvicorn lea_parser.server:app --port 8600
"""

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from lea_parser.database import Base, engine
from lea_parser.metrics import MetricsMiddleware, metrics_endpoint

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        import lea_parser.models  # noqa: F401 — register all models
        Base.metadata.create_all(bind=engine)
        logger.info("database_ready")
    except Exception as e:
        logger.warning("database_init_failed", error=str(e))
    yield


app = FastAPI(
    title="LEA Parser Service",
    version="0.1.0",
    description="文件解析 + 分块 + 鉴权限流 + 用量计费",
    lifespan=lifespan,
)

app.add_middleware(MetricsMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

from lea_parser.api.admin import router as admin_router
from lea_parser.api.console import router as console_router
from lea_parser.api.parse import router as parse_router
from lea_parser.api.usage import router as usage_router

app.include_router(parse_router)
app.include_router(admin_router)
app.include_router(usage_router)
app.include_router(console_router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "lea-parser"}


# 8600 在 docker-compose.test-server.yml 里**没有 ports: 段**，只在 compose 网络内
# 可达，Prometheus 从同一个网络抓 —— 所以这里可以挂在业务端口上。
# lea-gateway 的指标之所以单开一个不发布的 9108，是因为它的业务端口确实对外发布了，
# /metrics 公开出去等于把端点清单、各端点流量、错误率和鉴权拒绝数一起交出去。
# 将来给 parser 加 ports: 或者挂公网 Ingress 的话，这条路由要跟着挪走。
@app.get("/metrics")
def metrics():
    return metrics_endpoint()
