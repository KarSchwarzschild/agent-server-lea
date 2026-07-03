"""终结节点 — 保存综述结果到数据库。"""

from __future__ import annotations

import structlog

from lea_apps.research.state import ReviewPhase, ReviewState

logger = structlog.get_logger()


async def finalize_node(state: ReviewState) -> dict:
    from lea_apps.research.models.models import ReviewTask
    from lea_server.database import SessionLocal

    task_id = state.get("task_id")
    if not task_id:
        return {"phase": ReviewPhase.FINALIZE}

    db = SessionLocal()
    try:
        task = db.query(ReviewTask).filter(ReviewTask.id == int(task_id)).first()
        if task:
            task.outline = state.get("outline", "")
            task.review_content = state.get("review_draft", "")
            task.status = "completed"
            task.phase = ReviewPhase.FINALIZE.value
            db.commit()
            logger.info("review_finalized", task_id=task_id)
    except Exception as exc:
        db.rollback()
        logger.error("finalize_failed", task_id=task_id, error=str(exc))
        return {"error": str(exc), "phase": ReviewPhase.FINALIZE}
    finally:
        db.close()

    return {"phase": ReviewPhase.FINALIZE}
