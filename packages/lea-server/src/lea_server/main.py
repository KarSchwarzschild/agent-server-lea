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
from lea_server.api.admin_agent_bindings import router as admin_agent_bindings_router
from lea_server.api.admin_agents import router as admin_agents_router
from lea_server.api.admin_knowledge import router as admin_knowledge_router
from lea_server.api.admin_mcp_servers import router as admin_mcp_servers_router
from lea_server.api.admin_skills import router as admin_skills_router
from lea_server.api.admin_teams import router as admin_teams_router
from lea_server.api.admin_memories import router as admin_memories_router
from lea_server.api.admin_models import router as admin_models_router
from lea_server.api.memories import router as memories_router
from lea_server.api.admin_tools import router as admin_tools_router
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
from lea_server.models.knowledge import Document, DocumentChunk, DocumentSection, KnowledgeBase, RetrievalModel  # noqa: F401
from lea_server.models.attachment import ChatAttachment  # noqa: F401
from lea_server.models.chat_session import ChatMessage, ChatSession, UserMemory  # noqa: F401
from lea_server.models.skill import Skill, SkillVersion, SkillTestRun  # noqa: F401
from lea_server.models.agent import Agent  # noqa: F401
from lea_server.models.agent_binding import AgentSkillBinding, AgentKbBinding, AgentToolBinding  # noqa: F401
from lea_server.models.mcp_server import MCPServer  # noqa: F401
from lea_server.models.tool import Tool  # noqa: F401
from lea_server.models.audit_log import AuditLog  # noqa: F401
from lea_server.models.agent_team import AgentTeam, AgentTeamMember  # noqa: F401
from lea_server.models.memory_config import MemoryConfig  # noqa: F401
from lea_server.engine.app_registry import AppRegistry
from lea_graph.memory import create_redis_checkpointer
from lea_graph.orchestrator.supervisor import build_supervisor_graph

logger = structlog.get_logger()
settings = get_settings()


_DEFAULT_ROLES = [
    {"code": "admin", "name": "管理员", "description": "系统管理员，可管理所有资源", "is_system": True},
    {"code": "user", "name": "普通用户", "description": "默认角色", "is_system": True},
]


def _ensure_chat_messages_extra_column() -> None:
    """dev 环境无痛升级：chat_messages 已存在但缺 extra 列时补一下。

    生产用 migrations/002_chat_messages_add_extra.sql；此处只兜底 SQLite/MySQL dev 库。
    """
    from sqlalchemy import inspect, text

    try:
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("chat_messages")}
        if "extra" in cols:
            return
        dialect = engine.dialect.name
        col_type = "TEXT" if dialect == "sqlite" else "JSON"
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE chat_messages ADD COLUMN extra {col_type}"))
        logger.info("chat_messages.extra_column_added", dialect=dialect)
    except Exception as e:
        logger.warning("chat_messages_extra_migrate_skipped", error=str(e))


def _ensure_agents_new_columns() -> None:
    """dev 环境无痛升级：agents 表已存在但缺 Sprint C 新列时补上。"""
    from sqlalchemy import inspect, text

    new_cols = {
        "avatar": "VARCHAR(200) DEFAULT ''",
        "welcome_message": "TEXT",
        "soul_prompt": "TEXT",
        "category": "VARCHAR(50) DEFAULT 'general'",
        "example_questions": "JSON",
        "sort_order": "INT DEFAULT 0",
    }
    try:
        inspector = inspect(engine)
        existing = {c["name"] for c in inspector.get_columns("agents")}
        dialect = engine.dialect.name
        with engine.begin() as conn:
            for col_name, col_def in new_cols.items():
                if col_name not in existing:
                    if dialect == "sqlite" and "JSON" in col_def:
                        col_def = "TEXT"
                    conn.execute(text(
                        f"ALTER TABLE agents ADD COLUMN {col_name} {col_def}"
                    ))
                    logger.info("agents_column_added", column=col_name)
    except Exception as e:
        logger.warning("agents_migrate_skipped", error=str(e))


def _ensure_user_memories_new_columns() -> None:
    """dev 环境无痛升级：user_memories 表缺记忆服务新列时补上。"""
    from sqlalchemy import inspect, text

    new_cols = {
        "tenant_id": "VARCHAR(64) DEFAULT 'default'",
        "category": "VARCHAR(32) DEFAULT 'fact'",
        "importance": "INT DEFAULT 5",
        "access_count": "INT DEFAULT 0",
        "last_accessed_at": "DATETIME",
        "source_session_id": "VARCHAR(64)",
        "embedding_synced": "BOOLEAN DEFAULT FALSE",
    }
    try:
        inspector = inspect(engine)
        if "user_memories" not in inspector.get_table_names():
            return
        existing = {c["name"] for c in inspector.get_columns("user_memories")}
        with engine.begin() as conn:
            for col_name, col_def in new_cols.items():
                if col_name not in existing:
                    conn.execute(text(
                        f"ALTER TABLE user_memories ADD COLUMN {col_name} {col_def}"
                    ))
                    logger.info("user_memories_column_added", column=col_name)
    except Exception as e:
        logger.warning("user_memories_migrate_skipped", error=str(e))


def _ensure_document_chunks_section_id() -> None:
    """dev 环境无痛升级：kb_document_chunks 缺 section_id 列时补上。"""
    from sqlalchemy import inspect, text

    try:
        inspector = inspect(engine)
        if "kb_document_chunks" not in inspector.get_table_names():
            return
        existing = {c["name"] for c in inspector.get_columns("kb_document_chunks")}
        if "section_id" in existing:
            return
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE kb_document_chunks ADD COLUMN section_id INT NULL"
            ))
            logger.info("document_chunks_section_id_added")
    except Exception as e:
        logger.warning("document_chunks_section_id_skipped", error=str(e))


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


def _seed_super_agent_and_bind_builtins() -> None:
    """启动时确保存在 super-agent；当该 agent 绑定关系为空时自动补上所有 builtin skill。

    绑定语义：
    - 首次创建 super-agent → 一次性绑所有 builtin
    - 老库已有 super-agent 但 bindings 表为空（升级过来的）→ 也补一次
    - agent 已有任何绑定（admin 管理过或首次绑好后重启）→ 永不覆盖
    - 边界：admin 主动清空所有绑定 → 下次重启会重绑（罕见 case，用文档说明）

    须在 `_sync_builtin_skills` 之后调用，否则 skills 表还是空的。
    """
    from lea_server.core.tenant import DEFAULT_TENANT_ID
    from lea_server.models.agent import Agent
    from lea_server.models.agent_binding import AgentSkillBinding
    from lea_server.models.skill import Skill

    db = SessionLocal()
    try:
        super_agent = (
            db.query(Agent)
            .filter(Agent.name == "super-agent", Agent.tenant_id == DEFAULT_TENANT_ID)
            .first()
        )
        is_new = super_agent is None
        if is_new:
            super_agent = Agent(
                name="super-agent",
                description="通用超级智能助手",
                tenant_id=DEFAULT_TENANT_ID,
                is_system=True,
                enabled=True,
                avatar="🤖",
                welcome_message="你好！我可以帮你搜索资料、生成课件、答疑、辩论等等。",
                soul_prompt="",
                category="general",
                example_questions=[
                    "帮我梳理一下最近的教育技术趋势",
                    "生成一份《人工智能导论》第一章课件",
                    "对比两种混合学习模型的优劣",
                ],
                sort_order=0,
            )
            db.add(super_agent)
            db.commit()
            db.refresh(super_agent)

        has_any = (
            db.query(AgentSkillBinding)
            .filter(AgentSkillBinding.agent_id == super_agent.id)
            .first()
            is not None
        )
        bound_names: list[str] = []
        if not has_any:
            builtin_skills = db.query(Skill).filter(Skill.is_builtin == True).all()  # noqa: E712
            for skill in builtin_skills:
                db.add(
                    AgentSkillBinding(
                        agent_id=super_agent.id,
                        skill_id=skill.id,
                        priority=100,
                        enabled=True,
                    )
                )
                bound_names.append(skill.name)
            db.commit()

        logger.info(
            "super_agent_ready",
            id=super_agent.id,
            newly_created=is_new,
            auto_bound_skills=bound_names,
        )
    except Exception as e:
        db.rollback()
        logger.error("seed_super_agent_failed", error=str(e))
    finally:
        db.close()


def _seed_demo_team() -> None:
    """首次启动时种一个示例专家团（3 位成员 Agent）。"""
    from lea_server.core.tenant import DEFAULT_TENANT_ID
    from lea_server.models.agent import Agent
    from lea_server.models.agent_team import AgentTeam, AgentTeamMember

    db = SessionLocal()
    try:
        existing = (
            db.query(Agent)
            .filter(Agent.name == "教育研究专家团", Agent.tenant_id == DEFAULT_TENANT_ID)
            .first()
        )
        if existing:
            return

        members_def = [
            ("文献检索专家", "📚", "擅长学术文献搜索、综述和文献分析", "负责资料搜索和文献综述"),
            ("数据分析专家", "📊", "擅长教育数据分析和统计解读", "负责数据分析和图表"),
            ("写作润色专家", "✍️", "擅长学术写作、报告撰写和语言润色", "负责报告撰写和润色"),
        ]

        member_agents = []
        for name, avatar, soul, _ in members_def:
            a = Agent(
                name=name, tenant_id=DEFAULT_TENANT_ID, is_system=True, enabled=True,
                avatar=avatar, soul_prompt=soul, category="expert", description=soul,
            )
            db.add(a)
            member_agents.append(a)
        db.flush()

        facade = Agent(
            name="教育研究专家团", tenant_id=DEFAULT_TENANT_ID, is_system=True,
            enabled=True, avatar="🎓", category="team",
            description="三位教育研究专家协同工作：文献检索、数据分析、写作润色",
            welcome_message="我是教育研究专家团，由三位专家协作回答你的问题。",
            example_questions=["帮我调研一下翻转课堂最新研究进展", "分析这份问卷数据并写个总结报告"],
        )
        db.add(facade)
        db.flush()

        team = AgentTeam(agent_id=facade.id, strategy="parallel", max_subtasks=5)
        db.add(team)
        db.flush()

        for i, (_, _, _, role) in enumerate(members_def):
            db.add(AgentTeamMember(
                team_id=team.id, member_agent_id=member_agents[i].id,
                role_description=role, sort_order=i,
            ))

        db.commit()
        logger.info("demo_team_seeded", team_id=team.id, facade_id=facade.id)
    except Exception as e:
        db.rollback()
        logger.warning("seed_demo_team_skipped", error=str(e))
    finally:
        db.close()


def _sync_builtin_tools() -> None:
    """启动时把 lea_graph.tools 里的内置 BaseTool 元数据同步到 tools 表。

    与 _sync_builtin_skills 同为 insert_only 语义：
    - 已存在的 tool.name 仅刷新 description / input_schema（保留 admin 改过的 display_name / category / is_enabled）
    - 新的：INSERT + is_builtin=True + is_enabled=True
    """
    from lea_graph.tools import get_platform_tools
    from lea_server.services.tool_service import ToolService

    entries = []
    for tool in get_platform_tools():
        try:
            schema = {}
            if hasattr(tool, "args_schema") and tool.args_schema:
                schema = tool.args_schema.model_json_schema()
        except Exception:
            schema = {}
        entries.append({
            "name": tool.name,
            "display_name": getattr(tool, "display_name", None) or tool.name,
            "description": tool.description or "",
            "category": getattr(tool, "category", "general"),
            "input_schema": schema,
        })

    if not entries:
        logger.info("tool_sync_skipped", reason="no builtin tools found")
        return

    db = SessionLocal()
    try:
        svc = ToolService(db)
        stats = svc.sync_builtin_tools(registry_tools=entries)
        logger.info("builtin_tools_synced_at_startup", **stats)
    except Exception as e:
        db.rollback()
        logger.error("tool_sync_failed_at_startup", error=str(e))
    finally:
        db.close()


def _sync_builtin_skills(registry) -> None:
    """启动时将 builtin skill.md 同步到 DB（默认 insert_only，保护 UI 编辑权）。

    - 扫描 lea_graph/skills/builtin/**/skill.md
    - Anthropic Skills 4-key 格式解析
    - insert_only 模式：已存在的 skill 完全跳过（连 references/scripts 都不重刷）
    - upsert 模式仅供管理员通过 POST /admin/skills/sync?mode=upsert 触发
    """
    from pathlib import Path

    import lea_graph
    from lea_graph.skills.bundle_store import SkillBundleStore
    from lea_graph.skills.loader import SkillLoader
    from lea_server.services.skill_service import SkillService

    builtin_dir = Path(lea_graph.__file__).resolve().parent / "skills" / "builtin"
    if not builtin_dir.exists():
        logger.warning("skill_builtin_dir_missing", path=str(builtin_dir))
        return

    loader = SkillLoader(skill_dirs=[builtin_dir])
    definitions = list(loader.load_all().values())
    if not definitions:
        logger.warning("skill_no_builtin_definitions_found", path=str(builtin_dir))
        return

    # SG-4: 打 bundle + push OBS + 回填 DB。store 拿不到就走无 bundle 兜底
    try:
        bundle_store = SkillBundleStore.from_env()
    except Exception as e:
        bundle_store = None
        logger.warning("skill_bundle_store_unavailable", error=str(e))

    db = SessionLocal()
    try:
        service = SkillService(db)
        stats = service.sync_builtins(definitions, mode="insert_only", bundle_store=bundle_store)
        logger.info("builtin_skills_synced_at_startup", **stats)
    except Exception as e:
        db.rollback()
        logger.error("skill_sync_failed_at_startup", error=str(e))
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
            _ensure_chat_messages_extra_column()
            _ensure_agents_new_columns()
            _ensure_user_memories_new_columns()
            _ensure_document_chunks_section_id()
            enable_tenant_filter(SessionLocal)
        except Exception as e:
            logger.error("database_init_failed", error=str(e))

        # 6.1 初始化默认角色 + admin 账号（super-agent 挪到 skill sync 之后）
        try:
            _seed_roles()
            _seed_admin_user()
        except Exception as e:
            logger.error("seed_failed", error=str(e))

        # 6.4 同步内置 Tool 元数据到数据库（tool 先于 skill，因 skill.required_tools 引用 tool.name）
        try:
            _sync_builtin_tools()
        except Exception as e:
            logger.error("tool_sync_failed", error=str(e))

        # 6.5 同步内置 Skill 到数据库
        try:
            _sync_builtin_skills(registry)
        except Exception as e:
            logger.error("skill_sync_failed", error=str(e))

        # 6.6 super-agent（首次创建时自动绑定所有 builtin skill）
        try:
            _seed_super_agent_and_bind_builtins()
        except Exception as e:
            logger.error("seed_super_agent_failed", error=str(e))

        # 6.7 示例专家团
        try:
            _seed_demo_team()
        except Exception as e:
            logger.warning("seed_demo_team_skipped", error=str(e))

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
# CORS: 开发期放行所有 localhost/127.0.0.1 端口（前端 vite dev server 端口经常变）
# 生产时应改回具体域名白名单
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
class UserPrefixStripMiddleware:
    """/api/user/* → /api/*，纯 ASGI 版本（避免 BaseHTTPMiddleware 缓冲 SSE 流）。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path.startswith("/api/user/"):
                new_path = "/api/" + path[len("/api/user/") :]
                scope["path"] = new_path
                scope["raw_path"] = new_path.encode("latin-1")
        await self.app(scope, receive, send)


app.add_middleware(TenantMiddleware)
app.add_middleware(RequestTraceMiddleware)
app.add_middleware(UserPrefixStripMiddleware)

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
app.include_router(admin_tools_router, prefix="/api")
app.include_router(admin_agents_router, prefix="/api")
app.include_router(admin_agent_bindings_router, prefix="/api")
app.include_router(admin_mcp_servers_router, prefix="/api")
app.include_router(admin_users_router, prefix="/api")
app.include_router(admin_teams_router, prefix="/api")
app.include_router(admin_memories_router, prefix="/api")
app.include_router(admin_models_router, prefix="/api")
app.include_router(memories_router, prefix="/api")
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
