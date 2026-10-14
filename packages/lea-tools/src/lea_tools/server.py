"""Tool Gateway — FastAPI 入口。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from lea_tools.db import Base, engine
from lea_tools.metrics import MetricsMiddleware, metrics_endpoint


@asynccontextmanager
async def lifespan(app: FastAPI):
    import structlog
    log = structlog.get_logger()
    try:
        Base.metadata.create_all(bind=engine)
        log.info("database tables ensured")
        from lea_tools.seed import seed_builtin_tools
        n = seed_builtin_tools()
        if n:
            log.info("builtin tools seeded", count=n)
    except Exception as e:
        log.warning("database not available, skipping table creation", error=str(e))

    from lea_tools.services.sandbox_session import get_sandbox_manager
    mgr = get_sandbox_manager()
    await mgr.start()
    yield
    await mgr.stop()


app = FastAPI(title="LEA Tool Gateway", version="0.1.0", lifespan=lifespan)

app.add_middleware(MetricsMiddleware)


# 只剩 MCP 的 /sync。原来这里还有 admin_tools / admin_perms / admin_creds 三组,
# 全部**没有授权**且 tenant_id 由调用方在请求体里自选 —— 已删除，配置类操作统一走
# lea-config（那边有 require_admin + 归属约束）。理由见 api/admin_mcp.py 的说明。
from lea_tools.api.admin_mcp import router as admin_mcp_router
from lea_tools.api.execute import router as execute_router
from lea_tools.api.files import router as files_router

# 内部缓存失效端点：供 lea-config 在工具 CRUD 后调用，清除 capabilities 缓存。
from lea_tools.api.internal_cache import router as internal_cache_router

# 只做出网的那一跳：lea-config 拿它去问远端有哪些工具，鉴权和写表在 lea-config 那边。
# 拆两跳的理由是**不让 lea-config 开始打用户填的任意 URL** —— 它今天一次外网都不打，
# egress 可以锁死。见 api/internal_mcp.py 的说明。
from lea_tools.api.internal_mcp import router as internal_mcp_router
from lea_tools.api.sandbox import router as sandbox_router
from lea_tools.api.session import router as session_router

app.include_router(session_router)
app.include_router(execute_router)
app.include_router(admin_mcp_router)
app.include_router(internal_mcp_router)
app.include_router(internal_cache_router)
app.include_router(sandbox_router)
app.include_router(files_router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "lea-tools"}


@app.get("/metrics")
def metrics():
    """Prometheus 指标端点"""
    return metrics_endpoint()



# 服务间鉴权。必须在 include_router 之后 —— middleware 是包在整个 app 外面的，
# 但装的时候要能看到最终的路由表（EXEMPT_PATHS 的判断依赖路径）。
#
# 放在 /health 定义之后也是有意的：那条是探针用的，被豁免，顺序上挨着看得清楚。
from lea_authkit import install_internal_auth  # noqa: E402

install_internal_auth(app, service="lea-tools")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("lea_tools.server:app", host="0.0.0.0", port=8400, reload=True)
