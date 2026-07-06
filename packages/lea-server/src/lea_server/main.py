"""
FastAPI 主应用入口 — LEA Platform。

lifespan 中完成：
1. 创建 Redis 连接 + 初始化基础设施（熔断器、限流器）
2. MCP Client 初始化（连接外部 MCP 服务器）
3. Kafka 事件总线初始化
4. 加载所有App（discover + register）
5. 建表（平台模型 + App模型）
6. 构建Supervisor编排图（Redis Checkpointer）
7. 挂载App的API路由
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from lea_server.api.admin import router as admin_router
from lea_server.api.admin_knowledge import router as admin_knowledge_router
from lea_server.api.admin_skills import router as admin_skills_router
from lea_server.api.admin_users import router as admin_users_router
from lea_server.api.agents import router as agents_router
from lea_server.api.artifact import router as artifact_router
from lea_server.api.attachment import router as attachment_router
from lea_server.api.auth import router as auth_router
from lea_server.api.chat import router as chat_router
from lea_server.api.health import router as health_router
from lea_server.api.knowledge import router as knowledge_router
from lea_server.api.session import router as session_router
from lea_server.api.super_agent import router as super_agent_router
from lea_server.api.user_skills import router as user_skills_router
from lea_server.config import LEA_ENV, get_settings
from lea_server.core.middleware import RequestTraceMiddleware, TenantMiddleware
from lea_server.core.tenant import enable_tenant_filter
from lea_server.database import Base, SessionLocal, engine
from lea_server.models.knowledge import Document, DocumentChunk, KnowledgeBase  # noqa: F401
from lea_server.models.attachment import ChatAttachment  # noqa: F401
from lea_server.models.chat_session import ChatMessage, ChatSession, UserMemory  # noqa: F401
from lea_server.models.skill import Skill  # noqa: F401
from lea_server.engine.app_registry import AppRegistry
from lea_graph.memory import create_redis_checkpointer
from lea_graph.orchestrator.supervisor import build_supervisor_graph

logger = structlog.get_logger()
settings = get_settings()


_DEFAULT_ROLES = [
    {"code": "admin", "name": "管理员", "description": "系统管理员，可管理所有资源", "is_system": True},
    {"code": "user", "name": "普通用户", "description": "默认角色", "is_system": True},
]


def _seed_roles() -> None:
    """启动时确保默认角色存在。"""
    from lea_server.models.models import Role

    db = SessionLocal()
    try:
        for role_data in _DEFAULT_ROLES:
            existing = db.query(Role).filter(Role.code == role_data["code"]).first()
            if not existing:
                db.add(Role(**role_data))
                logger.info("role_seeded", code=role_data["code"])
        db.commit()
    finally:
        db.close()


def _seed_admin_user() -> None:
    """启动时确保存在默认管理员账号。

    账号信息从环境变量读取，未配置则使用默认值：
    - LEA_ADMIN_USERNAME (默认 admin)
    - LEA_ADMIN_PASSWORD (默认 admin123)
    """
    import os

    from lea_server.core.auth import get_password_hash
    from lea_server.models.models import Role, User

    admin_username = os.getenv("LEA_ADMIN_USERNAME", "admin")
    admin_password = os.getenv("LEA_ADMIN_PASSWORD", "admin123")

    db = SessionLocal()
    try:
        admin_role = db.query(Role).filter(Role.code == "admin").first()
        if admin_role is None:
            return

        has_admin = (
            db.query(User)
            .join(User.roles)
            .filter(Role.code == "admin")
            .first()
        )
        if has_admin:
            return

        existing_user = db.query(User).filter(User.username == admin_username).first()
        if existing_user:
            if admin_role not in existing_user.roles:
                existing_user.roles.append(admin_role)
            logger.info("admin_role_assigned", username=admin_username)
        else:
            import uuid
            user = User(
                user_id=f"user_{uuid.uuid4().hex[:12]}",
                username=admin_username,
                password_hash=get_password_hash(admin_password),
                email="admin@lea-platform.local",
            )
            user_role = db.query(Role).filter(Role.code == "user").first()
            if user_role:
                user.roles.append(user_role)
            user.roles.append(admin_role)
            db.add(user)
            logger.info("admin_user_created", username=admin_username)

        db.commit()
    finally:
        db.close()


def _sync_builtin_skills(registry) -> None:
    """启动时将 builtin skill.md 同步到数据库。"""
    from pathlib import Path

    from lea_graph.skills.loader import SkillLoader
    from lea_server.services.skill_service import SkillService

    import lea_graph.skills as _skills_pkg
    builtin_dir = Path(_skills_pkg.__file__).parent / "builtin"
    if not builtin_dir.is_dir():
        logger.info("no_builtin_skills_dir")
        return

    loader = SkillLoader([str(builtin_dir)])

    definitions = list(loader.load_all().values())
    if not definitions:
        logger.info("no_builtin_skills_found")
        return

    db = SessionLocal()
    try:
        count = SkillService(db).sync_builtins(definitions)
        db.commit()
        logger.info("builtin_skills_synced", count=count)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理。"""
    logger.info("platform_starting", env=LEA_ENV.value)

    # 1. Redis 连接 + 基础设施初始化
    import redis.asyncio as aioredis

    from lea_graph.infra.rate_limiter import init_rate_limiter
    from lea_graph.infra.resilience import init_breaker_registry

    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await redis_client.ping()
        logger.info("redis_connected", url=settings.redis_url.split("@")[-1])
    except Exception as e:
        logger.warning("redis_unavailable", error=str(e))

    init_breaker_registry(redis_client)
    rate_limiter = init_rate_limiter(redis_client)
    app.state.redis = redis_client
    app.state.rate_limiter = rate_limiter

    # 2. MCP Client 初始化（连接外部 MCP 服务器）
    from lea_graph.mcp.client import init_mcp_client
    from lea_graph.mcp.settings import get_mcp_settings

    mcp_client = init_mcp_client()
    mcp_configs = get_mcp_settings().get_server_configs()
    await mcp_client.connect(mcp_configs)

    # 3. Kafka 事件总线
    from lea_server.events import get_producer

    producer = get_producer()
    app.state.producer = producer

    # 4. Redis Checkpointer（运行时状态续接）
    async with create_redis_checkpointer(settings.redis_url) as checkpointer:
        # 5. 加载所有 App（checkpointer 通过 services 传给需要的 App）
        registry = AppRegistry(services={"checkpointer": checkpointer})
        registry.discover_and_load()

        # 6. 建表 + 启用租户过滤
        try:
            Base.metadata.create_all(bind=engine)
            enable_tenant_filter(SessionLocal)
        except Exception as e:
            logger.error("database_init_failed", error=str(e))

        # 6.1 初始化默认角色 + admin 账号
        try:
            _seed_roles()
            _seed_admin_user()
        except Exception as e:
            logger.error("seed_failed", error=str(e))

        # 6.5 同步内置 Skill 到数据库
        try:
            _sync_builtin_skills(registry)
        except Exception as e:
            logger.error("skill_sync_failed", error=str(e))

        # 7. 构建 Supervisor 图
        supervisor = build_supervisor_graph(registry.app_agents, checkpointer)

        app.state.supervisor = supervisor
        app.state.registry = registry
        app.state.checkpointer = checkpointer

        # 8. 挂载App路由
        for name, router in registry.api_routers:
            app.include_router(router, prefix=f"/api/apps/{name}")

        logger.info("platform_ready", apps=list(registry.apps.keys()))

        yield

    # 清理
    await mcp_client.disconnect()
    producer.close()
    await redis_client.aclose()
    logger.info("platform_shutdown")


app = FastAPI(
    title="LEA Platform",
    description="终身教育AI Agent平台",
    version="0.1.0",
    lifespan=lifespan,
)

# 中间件（注册顺序与执行顺序相反：后注册的先执行）
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
app.add_middleware(TenantMiddleware)
app.add_middleware(RequestTraceMiddleware)


@app.middleware("http")
async def strip_user_prefix(request, call_next):
    """/api/user/* → /api/*，让前端 user 命名空间指向平台通用路由。

    admin 前缀 (/api/admin/*) 保持不变。
    """
    path = request.url.path
    if path.startswith("/api/user/"):
        new_path = "/api/" + path[len("/api/user/") :]
        request.scope["path"] = new_path
        request.scope["raw_path"] = new_path.encode()
    return await call_next(request)

# 平台级路由
app.include_router(health_router)
app.include_router(auth_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(session_router, prefix="/api")
app.include_router(attachment_router, prefix="/api")
app.include_router(artifact_router)
app.include_router(admin_router, prefix="/api")
app.include_router(admin_knowledge_router, prefix="/api")
app.include_router(admin_skills_router, prefix="/api")
app.include_router(admin_users_router, prefix="/api")
app.include_router(knowledge_router, prefix="/api")
app.include_router(user_skills_router, prefix="/api")
app.include_router(super_agent_router, prefix="/api")
app.include_router(agents_router, prefix="/api")
# 让 /api/super-agent/chat/stream 复用 chat_router 的 /chat/stream 实现
app.include_router(chat_router, prefix="/api/super-agent")


@app.get("/")
async def root():
    return {
        "name": "LEA Platform",
        "version": "0.1.0",
        "env": LEA_ENV.value,
    }
