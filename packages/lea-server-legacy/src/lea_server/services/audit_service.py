"""审计日志写入 & 查询服务。

写入设计：
- write_event(session, event) —— 同步写入。调用方通过传入的 SA session 控制事务。
- log_event(engine, event) —— 独立事务写入。用于不方便共享 session 的地方
  （如 supervisor 图内部 fire-and-forget）。
- 支持 bulk：一次 chat 交互会产生多条事件，用 write_events 一次落库。

查询设计：
- 按 user/session/agent/tool_name/event_type/time_range 组合过滤
- 默认按 created_at 倒序，limit/offset 分页
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import and_, desc
from sqlalchemy.orm import Session

from lea_server.models.audit_log import AuditAction, AuditEventType, AuditLog


@dataclass
class AuditEvent:
    """待写入的一条审计事件 —— DTO，与 ORM 解耦便于跨进程传递。"""

    tenant_id: str
    user_id: str
    event_type: str
    session_id: str | None = None
    agent_id: int | None = None
    skill_name: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    content_redacted: str | None = None
    content_hash: str | None = None
    action: str = AuditAction.ALLOW
    reason: str | None = None
    pii_kinds: list[str] = field(default_factory=list)
    injection_patterns: list[str] = field(default_factory=list)
    status: str = "success"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> AuditLog:
        return AuditLog(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            session_id=self.session_id,
            agent_id=self.agent_id,
            event_type=self.event_type,
            skill_name=self.skill_name,
            tool_name=self.tool_name,
            tool_call_id=self.tool_call_id,
            content_redacted=self.content_redacted,
            content_hash=self.content_hash,
            action=self.action,
            reason=self.reason,
            pii_kinds=",".join(self.pii_kinds) if self.pii_kinds else None,
            injection_patterns=",".join(self.injection_patterns) if self.injection_patterns else None,
            status=self.status,
            extra=json.dumps(self.extra, ensure_ascii=False) if self.extra else None,
        )


def content_sha256(text: str | None) -> str | None:
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class AuditService:
    """审计写入与查询 —— 无状态服务，绑 SA session 使用。"""

    def __init__(self, db: Session):
        self.db = db

    def write_event(self, event: AuditEvent) -> AuditLog:
        row = event.to_row()
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def write_events(self, events: list[AuditEvent]) -> int:
        rows = [e.to_row() for e in events]
        self.db.bulk_save_objects(rows)
        self.db.commit()
        return len(rows)

    def query(
        self,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        agent_id: int | None = None,
        tool_name: str | None = None,
        event_type: str | None = None,
        action: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[AuditLog], int]:
        q = self.db.query(AuditLog)
        conds = []
        if tenant_id:
            conds.append(AuditLog.tenant_id == tenant_id)
        if user_id:
            conds.append(AuditLog.user_id == user_id)
        if session_id:
            conds.append(AuditLog.session_id == session_id)
        if agent_id is not None:
            conds.append(AuditLog.agent_id == agent_id)
        if tool_name:
            conds.append(AuditLog.tool_name == tool_name)
        if event_type:
            conds.append(AuditLog.event_type == event_type)
        if action:
            conds.append(AuditLog.action == action)
        if since:
            conds.append(AuditLog.created_at >= since)
        if until:
            conds.append(AuditLog.created_at <= until)

        if conds:
            q = q.filter(and_(*conds))

        total = q.count()
        rows = q.order_by(desc(AuditLog.created_at)).offset((page - 1) * size).limit(size).all()
        return rows, total

    def delete_for_user(self, user_id: str, tenant_id: str) -> int:
        """数据主体权利（被遗忘权）—— 按 user_id 删除所有审计。"""
        n = (
            self.db.query(AuditLog)
            .filter(AuditLog.user_id == user_id, AuditLog.tenant_id == tenant_id)
            .delete()
        )
        self.db.commit()
        return n


# ── 便捷构造函数（配合 lea_graph.security 使用）────────────

def event_user_message(
    tenant_id: str,
    user_id: str,
    session_id: str,
    agent_id: int | None,
    raw_text: str,
    redacted_text: str,
    pii_kinds: list[str],
) -> AuditEvent:
    return AuditEvent(
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=session_id,
        agent_id=agent_id,
        event_type=AuditEventType.USER_MESSAGE,
        content_redacted=redacted_text,
        content_hash=content_sha256(raw_text),
        pii_kinds=pii_kinds,
    )


def event_injection_hit(
    tenant_id: str,
    user_id: str,
    session_id: str,
    agent_id: int | None,
    risk_score: float,
    patterns: list[str],
    action: str,
    reason: str,
) -> AuditEvent:
    return AuditEvent(
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=session_id,
        agent_id=agent_id,
        event_type=AuditEventType.INJECTION_HIT,
        action=action,
        reason=reason,
        injection_patterns=patterns,
        status="blocked" if action == AuditAction.BLOCK else "success",
        extra={"risk_score": risk_score},
    )


def event_tool_call(
    tenant_id: str,
    user_id: str,
    session_id: str,
    agent_id: int | None,
    tool_name: str,
    tool_call_id: str,
    input_summary: str,
) -> AuditEvent:
    return AuditEvent(
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=session_id,
        agent_id=agent_id,
        event_type=AuditEventType.TOOL_CALL,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        content_redacted=input_summary,
    )
