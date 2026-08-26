"""综述 API — CRUD + 启动 + HITL 恢复（SSE 流式）。"""

from __future__ import annotations

import json
import uuid
from urllib.parse import quote

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from langgraph.types import Command
from sqlalchemy.orm import Session

from lea_apps.research.api.schemas import ReviewCreate, ReviewOut, ReviewResume
from lea_server.core.deps import get_current_tenant, get_current_user
from lea_server.core.tenant import TenantContext
from lea_server.database import get_db
from lea_server.models.models import User

logger = structlog.get_logger()

router = APIRouter(tags=["research"])

_review_graph = None

_GRAPH_NODES = [
    "init", "decompose_topic", "search", "hitl_search",
    "analyze", "hitl_analysis", "outline", "hitl_outline",
    "generate", "hitl_final", "finalize",
]


def _get_graph(checkpointer):
    """延迟初始化综述图，复用 lifespan 创建的 checkpointer。"""
    global _review_graph
    if _review_graph is None:
        from lea_apps.research.agents.review_graph import build_review_graph
        _review_graph = build_review_graph(checkpointer=checkpointer)
    return _review_graph


# ── CRUD ──


@router.get("/reviews", response_model=list[ReviewOut])
async def list_reviews(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(get_current_tenant),
):
    from lea_apps.research.models.models import ReviewTask

    return (
        db.query(ReviewTask)
        .filter(ReviewTask.tenant_id == tenant.tenant_id)
        .order_by(ReviewTask.created_at.desc())
        .limit(50)
        .all()
    )


@router.post("/reviews", response_model=ReviewOut)
async def create_review(
    data: ReviewCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(get_current_tenant),
):
    from lea_apps.research.models.models import ReviewTask

    task = ReviewTask(
        title=data.title,
        topic=data.topic,
        requirements=data.requirements,
        tenant_id=tenant.tenant_id,
        thread_id=str(uuid.uuid4()),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.get("/reviews/{review_id}", response_model=ReviewOut)
async def get_review(
    review_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(get_current_tenant),
):
    from lea_apps.research.models.models import ReviewTask

    task = (
        db.query(ReviewTask)
        .filter(ReviewTask.id == review_id, ReviewTask.tenant_id == tenant.tenant_id)
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="综述任务不存在")
    return task


@router.delete("/reviews/{review_id}")
async def delete_review(
    review_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(get_current_tenant),
):
    from lea_apps.research.models.models import ReviewTask

    task = (
        db.query(ReviewTask)
        .filter(ReviewTask.id == review_id, ReviewTask.tenant_id == tenant.tenant_id)
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="综述任务不存在")
    db.delete(task)
    db.commit()
    return {"status": "deleted"}


# ── 流程控制 ──


async def _stream_graph(graph, input_or_command, config):
    """执行图并以 SSE 流式返回节点更新。"""
    try:
        async for event in graph.astream(
            input_or_command, config=config, stream_mode="updates"
        ):
            yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
    except Exception as exc:
        logger.error("graph_stream_error", error=str(exc), exc_info=True)
        yield f"data: {json.dumps({'type': 'error', 'message': str(exc)}, ensure_ascii=False)}\n\n"

    snapshot = await graph.aget_state(config)
    if snapshot.next:
        interrupt_info: dict = {"type": "interrupt", "waiting_at": list(snapshot.next)}
        if snapshot.tasks:
            for t in snapshot.tasks:
                if t.interrupts:
                    interrupt_info["payload"] = t.interrupts[0].value
                    break
        yield f"data: {json.dumps(interrupt_info, ensure_ascii=False, default=str)}\n\n"

    yield 'data: {"type": "done"}\n\n'


@router.post("/reviews/{review_id}/start")
async def start_review(
    review_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(get_current_tenant),
):
    """启动综述生成流程，返回 SSE 流。"""
    from lea_apps.research.models.models import ReviewTask

    task = (
        db.query(ReviewTask)
        .filter(ReviewTask.id == review_id, ReviewTask.tenant_id == tenant.tenant_id)
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="综述任务不存在")
    if task.status not in ("pending", "error", "running"):
        raise HTTPException(status_code=400, detail=f"任务状态不允许启动: {task.status}")

    task.status = "running"
    db.commit()

    try:
        from lea_server.events import get_producer

        get_producer().send_audit(
            action="review_start",
            tenant_id=tenant.tenant_id,
            user_id=user.user_id,
            review_id=review_id,
            topic=task.topic,
        )
    except Exception:
        pass

    graph = _get_graph(request.app.state.checkpointer)
    config = {"configurable": {"thread_id": task.thread_id}}

    # 检查是否有已存在的 checkpoint — 支持中断恢复
    snapshot = await graph.aget_state(config)
    if snapshot.values and snapshot.next:
        # 图在 HITL 中断处暂停 — 通知前端需要 resume 而非 start
        logger.info("review_resumable", review_id=review_id, waiting_at=list(snapshot.next))
        interrupt_info: dict = {"type": "interrupt", "waiting_at": list(snapshot.next)}
        if snapshot.tasks:
            for t in snapshot.tasks:
                if t.interrupts:
                    interrupt_info["payload"] = t.interrupts[0].value
                    break

        async def _send_interrupt():
            yield f"data: {json.dumps(interrupt_info, ensure_ascii=False, default=str)}\n\n"
            yield 'data: {"type": "done"}\n\n'

        return StreamingResponse(_send_interrupt(), media_type="text/event-stream")

    if snapshot.values:
        # checkpoint 存在但不在中断处（可能中途崩溃了）— 传 None 重跑当前节点
        logger.info("review_continue", review_id=review_id)
        input_or_command = None
    else:
        # 全新任务 — 传入初始状态
        input_or_command = {
            "task_id": str(task.id),
            "tenant_id": tenant.tenant_id,
            "topic": task.topic,
            "requirements": task.requirements,
        }

    return StreamingResponse(
        _stream_graph(graph, input_or_command, config),
        media_type="text/event-stream",
    )


@router.post("/reviews/{review_id}/resume")
async def resume_review(
    review_id: int,
    data: ReviewResume,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(get_current_tenant),
):
    """HITL 恢复 — 用户审批后继续流程，返回 SSE 流。"""
    from lea_apps.research.models.models import ReviewTask

    task = (
        db.query(ReviewTask)
        .filter(ReviewTask.id == review_id, ReviewTask.tenant_id == tenant.tenant_id)
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="综述任务不存在")

    try:
        from lea_server.events import get_producer

        get_producer().send_audit(
            action="review_resume",
            tenant_id=tenant.tenant_id,
            user_id=user.user_id,
            review_id=review_id,
            approved=data.approved,
        )
    except Exception:
        pass

    graph = _get_graph(request.app.state.checkpointer)
    config = {"configurable": {"thread_id": task.thread_id}}
    resume_value = {"approved": data.approved, "feedback": data.feedback}

    return StreamingResponse(
        _stream_graph(graph, Command(resume=resume_value), config),
        media_type="text/event-stream",
    )


# ── 状态恢复 ──


@router.get("/reviews/{review_id}/checkpoint")
async def get_review_state(
    review_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(get_current_tenant),
):
    """从 Redis checkpoint 恢复综述执行状态（支持刷新后恢复 UI）。"""
    from lea_apps.research.models.models import ReviewTask

    task = (
        db.query(ReviewTask)
        .filter(ReviewTask.id == review_id, ReviewTask.tenant_id == tenant.tenant_id)
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="综述任务不存在")

    graph = _get_graph(request.app.state.checkpointer)
    config = {"configurable": {"thread_id": task.thread_id}}
    snapshot = await graph.aget_state(config)

    if not snapshot.values:
        return {"has_state": False}

    state = snapshot.values
    waiting_at = list(snapshot.next) if snapshot.next else []

    node_statuses = {}
    if waiting_at:
        wait_idx = _GRAPH_NODES.index(waiting_at[0]) if waiting_at[0] in _GRAPH_NODES else -1
        for i, node in enumerate(_GRAPH_NODES):
            if i < wait_idx:
                node_statuses[node] = "completed"
            elif node == waiting_at[0]:
                node_statuses[node] = "waiting"
            else:
                node_statuses[node] = "pending"
    else:
        for node in _GRAPH_NODES:
            node_statuses[node] = "completed"

    interrupt_payload = None
    if snapshot.tasks:
        for t in snapshot.tasks:
            if t.interrupts:
                interrupt_payload = t.interrupts[0].value
                break

    logs = []
    if state.get("search_results"):
        logs.append({"node": "search", "data": {"search_results": state["search_results"]}})
    if state.get("analyses"):
        logs.append({"node": "analyze", "data": {"analyses": state["analyses"]}})
    if state.get("outline"):
        logs.append({"node": "outline", "data": {"outline": state["outline"]}})
    if state.get("review_draft"):
        logs.append({"node": "generate", "data": {"review_draft": state["review_draft"]}})

    return {
        "has_state": True,
        "node_statuses": node_statuses,
        "waiting_at": waiting_at,
        "interrupt_payload": interrupt_payload,
        "logs": logs,
    }


# ── 导出 ──


@router.get("/reviews/{review_id}/export")
async def export_review(
    review_id: int,
    fmt: str = Query("docx", pattern="^(docx|pdf|latex|md)$"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(get_current_tenant),
):
    """导出综述为指定格式（实时转换，无状态）。"""
    from lea_apps.research.models.models import ReviewTask

    task = (
        db.query(ReviewTask)
        .filter(ReviewTask.id == review_id, ReviewTask.tenant_id == tenant.tenant_id)
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="综述任务不存在")
    if not task.review_content:
        raise HTTPException(status_code=400, detail="综述内容为空，请先完成生成")

    from lea_graph.services.pandoc_converter import PandocConverter

    converter = PandocConverter()
    file_bytes = await converter.convert(task.review_content, fmt, title=task.title)

    ext = converter.get_extension(fmt)
    mime = converter.get_mime_type(fmt)
    filename = f"{task.title}{ext}"
    encoded = quote(filename)

    return Response(
        content=file_bytes,
        media_type=mime,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded}",
        },
    )
