"""业务级审计日志 —— 中转平台救不了的业务实体追溯。

**分工前提**：上游中转平台已做调用级审计（API Key + model + tokens）。
本表承担业务实体串联：user × session × agent × tool × skill × intent × injection。

场景：
- 合规追溯：某用户在某时段发过什么、触发过哪些 tool
- 越权检测：agent 是否调了未绑定的 tool
- 数据主体权利（被遗忘权）：按 user_id 删除所有历史
- 安全 forensics：谁试图窃取 system prompt、绕过边界

关键约束：
- content_redacted 存脱敏后文本（合规友好，允许审计员查看）
- content_hash 存原文 sha256 全 hash（合规存证但不落原文，供事后对齐）
- 索引以 user_id / session_id / event_type / created_at 为核心过滤维度
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, String, Text, func

from lea_server.core.tenant import TenantMixin
from lea_server.database import Base


class AuditEventType:
    """事件类型枚举 —— 用常量避免拼写错误。"""

    USER_MESSAGE = "user_message"
    AI_MESSAGE = "ai_message"
    INTENT_ROUTED = "intent"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    LLM_CALL = "llm_call"
    INJECTION_HIT = "injection_hit"
    PII_DETECTED = "pii_detected"
    MODERATION_BLOCK = "moderation_block"
    ERROR = "error"


class AuditAction:
    """安全策略动作 —— 与 injection.InjectionAction 对齐（但独立，避免跨包耦合）。"""

    ALLOW = "allow"
    WARN = "warn"
    BLOCK = "block"


class AuditLog(TenantMixin, Base):
    """业务审计日志一行 = 一次业务事件。"""

    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)

    # 业务实体链
    user_id = Column(String(64), index=True, nullable=False)
    session_id = Column(String(64), index=True, nullable=True)
    agent_id = Column(Integer, index=True, nullable=True)

    # 事件本身
    event_type = Column(String(40), index=True, nullable=False)
    skill_name = Column(String(80), nullable=True)
    tool_name = Column(String(80), index=True, nullable=True)
    tool_call_id = Column(String(80), nullable=True)

    # 内容（合规脱敏 + 原文 hash 存证）
    content_redacted = Column(Text, nullable=True)
    content_hash = Column(String(64), nullable=True)

    # 安全事件字段
    action = Column(String(20), default=AuditAction.ALLOW)
    reason = Column(String(500), nullable=True)
    pii_kinds = Column(String(200), nullable=True)          # 逗号分隔，如 "phone,email"
    injection_patterns = Column(String(500), nullable=True) # 逗号分隔，如 "ignore-prev-cn,leak-prompt-cn"

    # 结果
    status = Column(String(20), default="success")  # success | failed | blocked
    extra = Column(Text, nullable=True)             # 扩展 JSON 字符串
