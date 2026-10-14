"""LEA Config — FastAPI 应用入口。"""

from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from lea_authkit import install_internal_auth

import lea_config.models  # noqa: F401 — register all models with Base.metadata
from lea_config.database import Base, engine
from lea_config.metrics import MetricsMiddleware, metrics_endpoint


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="LEA Config Service", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(MetricsMiddleware)

# Register routers — 统一资源路径，无 /admin 前缀
from lea_config.api.agents import router as agents_router
from lea_config.api.audit_events import router as audit_events_router
from lea_config.api.console_context import router as console_context_router
from lea_config.api.internal import router as internal_router
from lea_config.api.internal_governance import router as internal_governance_router
from lea_config.api.llm_models import router as llm_models_router
from lea_config.api.mcp_servers import router as mcp_servers_router
from lea_config.api.models import router as models_router
from lea_config.api.providers import router as providers_router
from lea_config.api.publication_requests import router as publication_requests_router
from lea_config.api.publication_resources import router as publication_resources_router
from lea_config.api.publications import router as publications_router
from lea_config.api.resource_checks import router as resource_checks_router
from lea_config.api.retrieval_models_public import router as retrieval_models_public_router
from lea_config.api.skills import router as skills_router
from lea_config.api.teams import router as teams_router
from lea_config.api.tools import router as tools_router

app.include_router(agents_router)          # /agents
app.include_router(skills_router)          # /skills
app.include_router(tools_router)           # /tools
app.include_router(teams_router)           # /teams
app.include_router(internal_router)        # /internal
app.include_router(internal_governance_router)
# 用户、角色由外部租户系统维护。本地旧模型保留用于兼容数据和迁移，但不再暴露 CRUD。
app.include_router(models_router)          # /retrieval/models（embedding/reranker/vlm/asr，admin）
app.include_router(llm_models_router)      # /llm-models（可选聊天模型，登录即可读）
app.include_router(retrieval_models_public_router)  # /retrieval-models（embedding/reranker/vlm 选择器，登录即可读）
app.include_router(mcp_servers_router)     # /mcp-servers
app.include_router(publication_requests_router)  # /publication-requests
app.include_router(publication_resources_router)
app.include_router(publications_router)
app.include_router(resource_checks_router)
app.include_router(providers_router)
app.include_router(audit_events_router)      # /audit-events
app.include_router(console_context_router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    """Prometheus 指标端点"""
    return metrics_endpoint()



# 服务间鉴权挂在最后 —— 见 lea_authkit/internal_auth.py 顶部关于"为什么内网也要挡"。
#
# ⚠️ 这个服务有两类调用方，都在 lea-agent 里，**必须两个都带上头**：
#   - runtime/config_loader.go  → /internal/agent-config（走 runtime 的共享 httpClient，
#     由 runtime/internal_token.go 的 Transport 统一注入）
#   - router/proxy.go           → /agents/*（httputil.ReverseProxy，**不经过那个
#     Transport**，得在 Director 里单独设）
# 漏掉后者的后果是前端的 Agent 列表在打开密钥之后整个 502/401，而且只在生产出现。
install_internal_auth(app, service="lea-config")


if __name__ == "__main__":
    uvicorn.run("lea_config.server:app", host="0.0.0.0", port=8500, reload=True)
