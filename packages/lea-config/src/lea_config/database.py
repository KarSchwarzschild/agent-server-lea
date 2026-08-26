"""数据库引擎 + 多租户基础。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings
from sqlalchemy import Column, String, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

load_dotenv(Path(__file__).parent.parent.parent.parent.parent / ".env")

DEFAULT_TENANT_ID = "default"


class TenantMixin:
    """多租户混入 — 业务表继承此类自动带 tenant_id。"""
    tenant_id = Column(String(50), nullable=False, index=True, default=DEFAULT_TENANT_ID)


# ── 归属模型 ──────────────────────────────────────────────────────────────
#
# 这个概念在库里被重新发明了四遍，编码各不相同：
#
#   skills           scope ∈ system/public/private  +  created_by
#   mcp_servers      owner_scope ∈ platform/user    +  owner_user_id
#   knowledge_bases  owner                          +  visibility     （在 lea-rag）
#   agents           光用 created_by
#   tools            没有
#
# 后果是同一批资源存在两套平行 CRUD（/agents 与 /admin/agents 等），
# 只有一套加了归属约束 —— 因为从来没有一个统一的东西可以让检查建在上面。
#
# 统一成 mcp_servers 那套命名（最清楚），并且把**两个正交的轴分开**：
#
#   owner_scope / owner_user_id  →  谁能**改**（可变性）
#   visibility（各资源自带）      →  谁能**看**（可见性）
#
# skills.scope 现在把两轴压在一列里：private 是归属声明，system/public 是可见性。
# 压在一起就没法表达"用户创建但发布给全租户"这种状态。
#
# created_by 保留，但它是**审计字段不是授权字段** —— 平台预置的 agent 的 created_by
# 是某个管理员的 user_id，语义却是"租户内共享"，用它做授权就把"谁创建的"和
# "谁能改"混成了一件事。

OWNER_SCOPE_PLATFORM = "platform"
"""平台/管理员提供：租户内所有人可见可用，只有管理员能改。owner_user_id 为空。"""

OWNER_SCOPE_USER = "user"
"""用户私有：只有 owner_user_id 那个人能改。是否可见由 visibility 另外决定。"""

OWNER_SCOPE_VALUES = (OWNER_SCOPE_PLATFORM, OWNER_SCOPE_USER)


class OwnerMixin:
    """归属混入 —— 让"这个对象属于谁"变成结构性的，而不是每张表各自记得加。

    默认 platform 是有意的：**新表忘了设归属时落到"管理员的东西"**，而不是落到
    某个用户名下或者空值。反过来默认 user 的话，owner_user_id 为空的行会变成
    "属于一个不存在的人"，那种行谁都改不了也谁都看不见，只能进库手改。
    """

    owner_scope = Column(
        String(20), nullable=False, default=OWNER_SCOPE_PLATFORM, index=True
    )
    owner_user_id = Column(String(64), nullable=True, index=True)


class Base(DeclarativeBase):
    pass


class DatabaseSettings(BaseSettings):
    host: str = Field(default="localhost", alias="DB_HOST")
    port: int = Field(default=3306, alias="DB_PORT")
    user: str = Field(default="root", alias="DB_USER")
    password: str = Field(default="", alias="DB_PASSWORD")
    name: str = Field(default="lea-platform-test", alias="DB_NAME")
    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def url(self) -> str:
        if self.host in ("sqlite", ""):
            return f"sqlite:///{self.name}.db"
        import urllib.parse
        pwd = urllib.parse.quote_plus(self.password)
        return f"mysql+pymysql://{self.user}:{pwd}@{self.host}:{self.port}/{self.name}?charset=utf8mb4"


@lru_cache
def get_settings() -> DatabaseSettings:
    return DatabaseSettings()


_settings = get_settings()
engine = create_engine(_settings.url, pool_pre_ping=True, pool_recycle=3600)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
