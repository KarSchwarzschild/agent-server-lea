"""安全预检节点 —— supervisor 图内的护栏。

流水线：注入检测 → 业务黑名单 → PII 脱敏审计
命中拦截 → 短路到 END + 写审计
未拦截   → 写 user_message 审计（脱敏版）+ 继续路由

**分工前提**：通用违规审核 + 通用注入 + 云 API 前 PII 脱敏 + 调用级审计
均由上游中转平台承担。本节点只做：
1. 落业务库前的脱敏审计（DBA/管理员看到的是脱敏文本）
2. 业务特化注入拦截（窃取 system prompt / 突破 agent 边界）
3. 业务黑名单（租户自定义禁词）
4. UI 回显脱敏由后续节点/API 用 text_for_user 处理
"""

from __future__ import annotations

import structlog
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.config import get_stream_writer

from lea_graph.security import (
    InjectionPolicy,
    PIIScrubber,
    SecurityContext,
    SecurityPipeline,
)
from lea_graph.state import PlatformState

logger = structlog.get_logger()

_pipeline: SecurityPipeline | None = None


def _get_pipeline() -> SecurityPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = SecurityPipeline(scrubber=PIIScrubber())
    return _pipeline


def _resolve_ctx(state: PlatformState, config: dict) -> SecurityContext:
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    thread_id = configurable.get("thread_id", "") or ""
    session_id = thread_id.split(":")[-1] if ":" in thread_id else thread_id
    mode = configurable.get("injection_policy_mode", "warn")
    return SecurityContext(
        tenant_id=configurable.get("tenant_id", "default"),
        user_id=state.get("user_id", ""),
        session_id=session_id,
        injection_policy=InjectionPolicy(mode=mode),
    )


def _write_audits(
    tenant_id: str,
    user_id: str,
    session_id: str,
    agent_id: int | None,
    raw_text: str,
    result,
) -> None:
    """同步写审计 —— fire-and-forget 语义（异常吞掉，不阻塞主链路）。

    lea_graph 反向依赖 lea_server 在本项目里已有先例（memory_nodes/knowledge 亦然）。
    如未来严格分层，可改为通过 config.configurable 传入 callback。
    """
    try:
        from lea_server.database import SessionLocal
        from lea_server.models.audit_log import AuditAction, AuditEventType
        from lea_server.services.audit_service import (
            AuditService,
            event_injection_hit,
            event_user_message,
        )

        events = [
            event_user_message(
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
                agent_id=agent_id,
                raw_text=raw_text,
                redacted_text=result.text_for_audit,
                pii_kinds=list((result.pii.kinds() if result.pii else set())),
            )
        ]
        if result.injection and result.injection.matched_patterns:
            events.append(
                event_injection_hit(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    session_id=session_id,
                    agent_id=agent_id,
                    risk_score=result.injection.risk_score,
                    patterns=result.injection.matched_patterns,
                    action=result.action.value,
                    reason=result.injection.reason or "",
                )
            )
        with SessionLocal() as db:
            AuditService(db).write_events(events)
    except Exception as e:
        logger.warning("audit_write_failed", error=str(e))


async def security_precheck_node(state: PlatformState, config) -> dict:
    """安全预检节点 —— 在 router 之前拦最新一条用户消息。"""
    user_messages = [m for m in state["messages"] if isinstance(m, HumanMessage)]
    if not user_messages:
        return {"security_action": "allow"}

    text = user_messages[-1].content or ""
    if not isinstance(text, str):
        # attachment 等复合消息，暂不检测
        return {"security_action": "allow"}

    ctx = _resolve_ctx(state, config)
    result = await _get_pipeline().run_before_llm(text, ctx)

    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    agent_id = configurable.get("agent_id")
    _write_audits(
        ctx.tenant_id, ctx.user_id, ctx.session_id, agent_id, text, result
    )

    if result.blocked:
        logger.info(
            "security_blocked",
            user_id=ctx.user_id,
            session_id=ctx.session_id,
            reason=result.block_reason,
        )
        writer = get_stream_writer()
        writer(
            {
                "type": "intent",
                "app": "security",
                "agent": "block",
                "skill_display_name": "安全策略拦截",
                "confidence": 1.0,
            }
        )
        return {
            "messages": [
                AIMessage(
                    content=f"很抱歉，本次消息因安全策略被拦截。原因：{result.block_reason}"
                )
            ],
            "security_action": "block",
        }

    return {"security_action": "allow"}


def security_gate(state: PlatformState) -> str:
    """条件边：security_action == 'block' → END，否则 → router。"""
    return "END" if state.get("security_action") == "block" else "router"
