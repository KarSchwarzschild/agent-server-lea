"""认证API — 登录、注册、用户信息。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from lea_server.core.auth import create_access_token, get_password_hash, verify_password
from lea_server.core.deps import get_current_user
from lea_server.database import get_db
from lea_server.models.models import Role, User

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    email: str = ""


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/login", response_model=Token)
async def login(req: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == req.username).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已禁用")

    user.last_login_at = datetime.now(timezone.utc)
    user.last_login_ip = request.client.host if request.client else ""
    db.commit()

    roles = [r.code for r in user.roles]
    token = create_access_token({"sub": user.user_id, "roles": roles})
    return Token(access_token=token)


@router.post("/register", response_model=Token)
async def register(req: RegisterRequest, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.username == req.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="用户名已存在")

    user = User(
        user_id=f"user_{uuid.uuid4().hex[:12]}",
        username=req.username,
        password_hash=get_password_hash(req.password),
        email=req.email,
    )
    db.add(user)

    default_role = db.query(Role).filter(Role.code == "user").first()
    if default_role:
        user.roles.append(default_role)

    db.commit()
    db.refresh(user)

    roles = [r.code for r in user.roles]
    token = create_access_token({"sub": user.user_id, "roles": roles})
    return Token(access_token=token)


@router.get("/me")
async def get_me(user: User = Depends(get_current_user)):
    return {
        "id": user.id,
        "user_id": user.user_id,
        "username": user.username,
        "email": user.email,
        "avatar": user.avatar,
        "roles": [{"code": r.code, "name": r.name} for r in user.roles],
    }
