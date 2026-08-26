"""共享测试 fixture — SQLite in-memory 隔离。

env 强制走 sqlite，避免污染真实 MySQL；每个 test function 独立 session。
"""

from __future__ import annotations

import os

# ── 必须在 import lea_server 前设置 env ──────────────────────────
os.environ.setdefault("LEA_ENV", "test")
os.environ["DB_HOST"] = "sqlite"

# ⚠️ 不能设成 ":memory:"。
#
# 各服务的 DatabaseSettings.url 在 host=="sqlite" 时返回 f"sqlite:///{name}.db"，
# 也就是把 DB_NAME 当**文件名**用。传 ":memory:" 会拼出 "sqlite:///:memory:.db" ——
# 一个字面上叫 ":memory:.db" 的文件，而 Windows 文件名不允许含冒号，于是
# sqlite3.OperationalError: unable to open database file。
#
# 而且 get_settings() 带 lru_cache，所以这个值是**进程级**的：全量跑测试时，
# 先 import 的模块把它缓存下来，后面用 lea_rag.db.engine 的测试（test_memory_scope）
# 就拿到那个畸形 URL 报错 —— 单独跑那个文件却是绿的。典型的测试间污染。
#
# 用一个真实文件名。需要真 in-memory 的 fixture 自己 create_engine
# ("sqlite:///:memory:")，那是合法写法（见下面 db_session）。
os.environ["DB_NAME"] = "lea-test-scratch"

os.environ.setdefault("SECRET_KEY", "test-secret")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture(scope="function")
def db_session():
    """独立 in-memory SQLite session，每个测试隔离。"""
    # 触发 model 注册 —— 让 Base.metadata 收集所有表
    from lea_server.database import Base
    from lea_server.models import (  # noqa: F401
        agent,
        agent_binding,
        attachment,
        chat_session,
        knowledge,
        models,
        skill,
        tool,  # AgentToolBinding 外键指向 tools，不导入则 create_all 不建表
    )

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def seed_agent(db_session):
    """建一个测试用 super-agent。"""
    from lea_server.models.agent import Agent

    agent = Agent(
        name="super-agent",
        description="test agent",
        tenant_id="default",
        is_system=True,
        enabled=True,
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)
    return agent


@pytest.fixture
def seed_skills(db_session):
    """建 3 个测试 skill：2 个内置 + 1 个用户创建。"""
    from lea_server.models.skill import Skill

    skills = [
        Skill(
            name="slide-generator",
            display_name="Slide Gen",
            description="Triggers: PPT, 演示文稿",
            model_tier="high",
            required_tools=[],
            pipeline_class="lea_graph.skills.pipelines.slide_generator.X",
            status="published",
            scope="system",
            is_builtin=True,
            tenant_id="default",
        ),
        Skill(
            name="slide-editor",
            display_name="Slide Edit",
            description="Triggers: 改PPT",
            model_tier="high",
            required_tools=[],
            pipeline_class="lea_graph.skills.pipelines.slide_editor.X",
            status="published",
            scope="system",
            is_builtin=True,
            tenant_id="default",
        ),
        Skill(
            name="user-skill-x",
            display_name="用户创建的",
            description="",
            model_tier="mid",
            required_tools=[],
            pipeline_class="",
            status="published",
            scope="public",
            is_builtin=False,
            tenant_id="default",
        ),
    ]
    for s in skills:
        db_session.add(s)
    db_session.commit()
    for s in skills:
        db_session.refresh(s)
    return skills


@pytest.fixture
def seed_kbs(db_session):
    """建 3 个测试知识库。"""
    from lea_server.models.knowledge import KnowledgeBase

    kbs = [
        KnowledgeBase(
            name=f"KB-{i}",
            description="",
            owner="test-user",
            visibility="private",
            tenant_id="default",
        )
        for i in range(1, 4)
    ]
    for kb in kbs:
        db_session.add(kb)
    db_session.commit()
    for kb in kbs:
        db_session.refresh(kb)
    return kbs
