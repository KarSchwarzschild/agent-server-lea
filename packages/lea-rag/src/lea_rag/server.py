"""LEA 统一检索服务 — 知识库 + 记忆 合一入口。

供 Go 执行引擎（云端/客户端）通过 HTTP REST 调用。
合并了知识库语义检索和用户记忆检索，共享 Milvus / Embedding 基础设施。
设计原则：无 LLM 调用、纯机械、亚秒级。

启动方式：
  uvicorn lea_rag.server:app --host 0.0.0.0 --port 8300

或通过 docker-compose 独立部署。

API 总览：
  GET  /health
  POST /kb/search           知识库混合检索
  POST /kb/sections         获取文档章节树
  POST /memory/search       语义检索用户记忆（多信号融合排序）
  POST /memory/add          添加记忆
  GET  /memory/list         列出用户记忆
  PUT  /memory/{id}         更新记忆
  DELETE /memory/{id}       删除记忆
  DELETE /memory/user/{uid} 清空用户记忆
  POST /memory/extract      从对话提取记忆（可选便利接口，推荐引擎侧提取）
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from lea_authkit import install_internal_auth
from lea_rag.metrics import MetricsMiddleware, metrics_endpoint
from lea_rag.server_admin_kb import router as admin_kb_router
from lea_rag.server_admin_memory import router as admin_memory_router
from lea_rag.server_kb import router as kb_router
from lea_rag.server_memory import router as memory_router
from lea_rag.server_user_kb import router as user_kb_router
from lea_rag.server_user_memory import router as user_memory_router
from lea_rag.server_governance import internal_router as governance_internal_router
from lea_rag.server_governance import router as governance_router

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("retrieval_service_starting")
    _ensure_db_tables()
    yield
    logger.info("retrieval_service_shutdown")


def _ensure_db_tables():
    """独立部署时确保 KB 相关表存在。"""
    try:
        from lea_rag.db import Base, engine
        from lea_rag.db.models import (  # noqa: F401
            Document, DocumentChunk, DocumentSection, KnowledgeBase, RetrievalModel,
        )
        from lea_rag.db.models import UserMemory, UserProfile  # noqa: F401

        Base.metadata.create_all(bind=engine)
        logger.info("db_tables_ensured")
        _seed_default_models()
    except Exception as exc:
        logger.warning("db_tables_skipped", error=str(exc))


def _seed_default_models():
    """首次启动时种入默认模型（如果 retrieval_models 表为空）。"""
    from lea_rag.db import SessionLocal
    from lea_rag.db.models import RetrievalModel
    from lea_rag.config import EmbeddingSettings, RerankerSettings, get_tenant_api_key

    db = SessionLocal()
    try:
        if db.query(RetrievalModel).count() > 0:
            return

        embed_cfg = EmbeddingSettings()
        reranker_cfg = RerankerSettings()
        api_key = get_tenant_api_key()

        defaults = [
            RetrievalModel(
                name=embed_cfg.model,
                model_type="embedding",
                api_base=embed_cfg.api_base,
                api_key=api_key,
                model_name=embed_cfg.model,
                dimension=embed_cfg.dimension,
                supports=["text"],
                tags=["中文", "默认"],
                enabled=True,
                is_default=True,
            ),
            RetrievalModel(
                name=reranker_cfg.model,
                model_type="reranker",
                api_base=reranker_cfg.api_base,
                model_name=reranker_cfg.model,
                supports=[],
                tags=["中文"],
                enabled=reranker_cfg.enabled,
                is_default=True,
            ),
            RetrievalModel(
                name="qwen3-vl-plus",
                model_type="vlm",
                api_base="https://tenantapi-ai.ouchn.edu.cn",
                api_key=api_key,
                model_name="qwen3-vl-plus",
                supports=["image"],
                tags=["多模态", "中文"],
                enabled=True,
                is_default=True,
            ),
        ]
        for m in defaults:
            db.add(m)
        db.commit()
        logger.info("default_models_seeded", count=len(defaults))
    except Exception as exc:
        db.rollback()
        logger.warning("seed_models_failed", error=str(exc))
    finally:
        db.close()


app = FastAPI(
    title="LEA Retrieval Service",
    description="统一检索服务 — 知识库混合检索 + 用户记忆语义检索",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(MetricsMiddleware)

app.include_router(kb_router)
app.include_router(memory_router)
app.include_router(admin_kb_router)
app.include_router(admin_memory_router)
app.include_router(user_kb_router)
app.include_router(user_memory_router)
app.include_router(governance_router)
app.include_router(governance_internal_router)


@app.get("/models")
async def list_available_models(model_type: str | None = None):
    """列出可用模型（供 Go 引擎创建 KB 时选择）。"""
    from lea_rag.db import SessionLocal
    from lea_rag.db.models import RetrievalModel

    db = SessionLocal()
    try:
        q = db.query(RetrievalModel).filter(RetrievalModel.enabled == True)
        if model_type:
            q = q.filter(RetrievalModel.model_type == model_type)
        models = q.order_by(RetrievalModel.model_type, RetrievalModel.name).all()
        return [
            {
                "name": m.name,
                "model_type": m.model_type,
                "model_name": m.model_name or m.name,
                "dimension": m.dimension,
                "supports": m.supports or [],
                "tags": m.tags or [],
                "is_default": m.is_default,
            }
            for m in models
        ]
    finally:
        db.close()


@app.get("/health")
async def health():
    return {"status": "ok", "service": "retrieval", "version": "0.1.0"}


@app.get("/metrics")
async def metrics():
    """Prometheus 指标端点"""
    return metrics_endpoint()



# 服务间鉴权挂在最后 —— 见 lea_authkit/internal_auth.py 顶部关于"为什么内网也要挡"。
# 这个服务的接口面里除了 /kb /memory，还有 /admin/knowledge-bases/*（建库删库）和
# /knowledge-bases、/memories（按 tenant_id 参数分租户，而那个参数是调用方说的）。
install_internal_auth(app, service="lea-rag")
