"""SuperAgent 前端专用 API 层。

lifelong-edu 前端全面使用 /api/user/super-agent/* 命名空间。经中间件剥前缀后
到达 /api/super-agent/*：
- /chat/stream 通过 chat_router 在 main.py 多 mount 复用
- /sessions/* 前端用扁平路径（无 /chat/），且 GET single 返回内嵌 messages
- /health /skills /stop /tools/result 是纯 stub 或 alias
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from lea_server.core.deps import get_current_user
from lea_server.database import get_db
from lea_server.models.chat_session import ChatMessage, ChatSession
from lea_server.models.models import User

router = APIRouter(prefix="/super-agent", tags=["super-agent"])


class SessionCreate(BaseModel):
    title: Optional[str] = None


class ToolResultBody(BaseModel):
    tool_use_id: str
    content: str
    is_error: bool = False


def _session_to_dict(s: ChatSession) -> dict:
    """把 ChatSession 转成前端 SuperAgentSession 形状。"""
    return {
        "session_id": s.id,
        "user_id": s.user_id,
        "title": s.title,
        "status": "archived" if s.is_archived else "active",
        "agent_id": None,
        "agent_name": None,
        "created_at": s.created_at.isoformat() if s.created_at else "",
        "updated_at": s.last_active_at.isoformat() if s.last_active_at else "",
        "message_count": s.message_count or 0,
    }


@router.get("/health")
def health():
    """SuperAgent 健康检查（前端启动时探活）。"""
    return {"status": "ok", "skills_count": 0, "version": "0.1.0"}


@router.get("/skills")
def get_skills(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """返回可用 skill 数组（前端 SkillSelector 用）。"""
    from lea_server.services.skill_service import SkillService

    svc = SkillService(db)
    skills, _ = svc.list_skills(user_id=user.user_id, page=1, size=100)
    return [
        {
            "name": s.name,
            "display_name": s.display_name or s.name,
            "description": s.description or "",
            "icon": (s.resources or {}).get("icon", "🛠️") if isinstance(s.resources, dict) else "🛠️",
            "triggers": s.trigger_keywords or [],
            "tools": s.allowed_tools or [],
            "enabled": bool(s.enabled),
        }
        for s in skills
    ]


@router.get("/sessions")
def list_sessions(
    status: Optional[str] = None,
    agent_id: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """返回 session 数组（非分页 wrapper，直接是数组）。"""
    query = db.query(ChatSession).filter(ChatSession.user_id == user.user_id)
    if status == "archived":
        query = query.filter(ChatSession.is_archived.is_(True))
    else:
        query = query.filter(ChatSession.is_archived.is_(False))
    sessions = (
        query.order_by(desc(ChatSession.last_active_at))
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_session_to_dict(s) for s in sessions]


@router.post("/sessions")
def create_session(
    body: SessionCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """新建 session。"""
    session = ChatSession(user_id=user.user_id, title=body.title or "新对话")
    db.add(session)
    db.commit()
    db.refresh(session)
    return _session_to_dict(session)


@router.get("/sessions/{session_id}")
def get_session_detail(
    session_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取 session 详情（含消息历史，内嵌）。"""
    session = (
        db.query(ChatSession)
        .filter(ChatSession.id == session_id, ChatSession.user_id == user.user_id)
        .first()
    )
    if session is None:
        return {"error": "session not found"}, 404
    msgs = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
        .all()
    )
    return {
        "session_id": session.id,
        "title": session.title,
        "messages": [
            {
                "message_id": str(m.id),
                "role": m.role,
                "content": m.content or "",
                "created_at": m.created_at.isoformat() if m.created_at else "",
            }
            for m in msgs
            if m.content
        ],
    }


@router.delete("/sessions/{session_id}")
def delete_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """归档 session。"""
    session = (
        db.query(ChatSession)
        .filter(ChatSession.id == session_id, ChatSession.user_id == user.user_id)
        .first()
    )
    if session is None:
        return {"message": "session not found"}
    session.is_archived = True
    db.commit()
    return {"message": "archived"}


@router.post("/sessions/{session_id}/stop")
async def stop_session(session_id: str, user: User = Depends(get_current_user)):
    """停止正在执行的会话：取消后台任务 + 向 EventSource 消费者广播 cancelled 事件。"""
    from lea_server.services.sse_bus import get_event_bus

    bus = get_event_bus()
    cancelled = await bus.cancel(session_id)
    return {"success": True, "session_id": session_id, "was_running": cancelled}


@router.post("/tools/result")
def submit_tool_result(
    body: ToolResultBody,
    user: User = Depends(get_current_user),
):
    """客户端工具执行结果回传（暂时是 stub，等 client_tool 事件闭环时再实现）。"""
    return {"message": "acknowledged"}
