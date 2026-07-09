"""共享测试 fixture — SQLite in-memory 隔离。

env 强制走 sqlite，避免污染真实 MySQL；每个 test function 独立 session。
"""

from __future__ import annotations

import os

# ── 必须在 import lea_server 前设置 env ──────────────────────────
os.environ.setdefault("LEA_ENV", "test")
os.environ["DB_HOST"] = "sqlite"
os.environ["DB_NAME"] = ":memory:"
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
            system_prompt="你是 PPT 专家",
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
            system_prompt="改 PPT 助手",
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
            system_prompt="",
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
