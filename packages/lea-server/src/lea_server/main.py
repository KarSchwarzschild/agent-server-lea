"""
FastAPI 主应用入口 — LEA Platform。

lifespan 中完成：
1. 加载所有App（discover + register）
2. 建表（平台模型 + App模型）
3. 构建Supervisor编排图
4. 挂载App的API路由
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from lea_server.api.admin import router as admin_router
from lea_server.api.auth import router as auth_router
from lea_server.api.chat import router as chat_router
from lea_server.api.health import router as health_router
from lea_server.config import LEA_ENV, get_settings
from lea_server.database import Base, engine
from lea_server.engine.app_registry import AppRegistry
from lea_graph.memory import create_memory_checkpointer
from lea_graph.orchestrator.supervisor import build_supervisor_graph

logger = structlog.get_logger()
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理。"""
    logger.info("platform_starting", env=LEA_ENV.value)

    # 1. 加载所有App
    registry = AppRegistry()
    registry.discover_and_load()

    # 2. 建表（DB不可用时跳过，允许无DB启动用于调试）
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as e:
        logger.error("database_init_failed", error=str(e))

    # 3. 构建Supervisor图
    checkpointer = create_memory_checkpointer()
    supervisor = build_supervisor_graph(registry.app_agents, checkpointer)

    app.state.supervisor = supervisor
    app.state.registry = registry
    app.state.checkpointer = checkpointer

    # 4. 挂载App路由
    for name, router in registry.api_routers:
        app.include_router(router, prefix=f"/api/apps/{name}")
        logger.info("app_router_mounted", app=name, prefix=f"/api/apps/{name}")

    logger.info("platform_ready", apps=list(registry.apps.keys()))

    yield

    logger.info("platform_shutdown")


app = FastAPI(
    title="LEA Platform",
    description="终身教育AI Agent平台",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:9001",
        "http://localhost:9002",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 平台级路由
app.include_router(health_router)
app.include_router(auth_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(admin_router, prefix="/api")


@app.get("/")
async def root():
    return {
        "name": "LEA Platform",
        "version": "0.1.0",
        "env": LEA_ENV.value,
    }
