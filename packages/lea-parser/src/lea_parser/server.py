"""LEA Parser Server — 文件处理微服务入口。

启动：uvicorn lea_parser.server:app --port 8600
"""

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from lea_parser.database import Base, engine

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

from lea_parser.api.parse import router as parse_router
from lea_parser.api.admin import router as admin_router
from lea_parser.api.usage import router as usage_router
from lea_parser.api.console import router as console_router

app.include_router(parse_router)
app.include_router(admin_router)
app.include_router(usage_router)
app.include_router(console_router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "lea-parser"}
