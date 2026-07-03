"""聊天附件模型 — 对话中上传的文件。"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, String, Text, func

from lea_server.core.tenant import TenantMixin
from lea_server.database import Base


class ChatAttachment(TenantMixin, Base):
    """对话附件 — 用户在聊天中上传的文件。"""

    __tablename__ = "chat_attachments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(50), nullable=False, index=True)
    session_id = Column(String(100), default="", index=True)
    filename = Column(String(300), nullable=False)
    file_key = Column(String(500), nullable=False)
    file_type = Column(String(20), default="")
    file_size = Column(Integer, default=0)
    media_type = Column(String(20), default="text")
    extracted_text = Column(Text, default="")
    created_at = Column(DateTime, default=func.now())
