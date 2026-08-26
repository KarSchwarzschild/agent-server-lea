"""User / Role 模型（对齐 lea-server 老副本，含 user_roles 关联表）。"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    func,
)
from sqlalchemy.orm import relationship

from lea_config.database import Base

user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("role_id", Integer, ForeignKey("roles.id"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(100), unique=True, nullable=False, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    tenant_id = Column(String(50), nullable=False, index=True, default="default")
    password_hash = Column(String(255), nullable=False)
    email = Column(String(200), default="")
    avatar = Column(String(500), default="")
    phone = Column(String(20), default="")
    is_active = Column(Boolean, default=True, index=True)
    last_login_at = Column(DateTime, nullable=True)
    last_login_ip = Column(String(50), default="")
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    roles = relationship("Role", secondary=user_roles, back_populates="users", lazy="joined")


class Role(Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    description = Column(String(500), default="")
    is_system = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())

    users = relationship("User", secondary=user_roles, back_populates="roles")
