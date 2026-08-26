"""LEA Config — FastAPI 应用入口。"""

from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from lea_config.database import Base, engine
from lea_authkit import install_internal_auth
import lea_config.models  # noqa: F401 — register all models with Base.metadata


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

# Register routers — 统一资源路径，无 /admin 前缀
from lea_config.api.agents import router as agents_router
from lea_config.api.internal import router as internal_router
from lea_config.api.mcp_servers import router as mcp_servers_router
from lea_config.api.models import router as models_router
from lea_config.api.skills import router as skills_router
from lea_config.api.teams import router as teams_router
from lea_config.api.tools import router as tools_router
from lea_config.api.users import users_router, roles_router

app.include_router(agents_router)          # /agents
app.include_router(skills_router)          # /skills
app.include_router(tools_router)           # /tools
app.include_router(teams_router)           # /teams
app.include_router(internal_router)        # /internal
app.include_router(users_router)           # /users
app.include_router(roles_router)           # /roles
app.include_router(models_router)          # /retrieval/models
app.include_router(mcp_servers_router)     # /mcp-servers


@app.get("/health")
def health():
    return {"status": "ok"}


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
