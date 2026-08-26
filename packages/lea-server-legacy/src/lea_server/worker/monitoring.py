"""
Celery 监控集成 — 任务状态查询 + Flower 配置。

提供 API 层查询任务状态的工具函数，以及 Flower 监控的配置。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from lea_server.worker.celery_app import celery_app


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    STARTED = "STARTED"
    PROGRESS = "PROGRESS"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    REVOKED = "REVOKED"


@dataclass
class TaskInfo:
    task_id: str
    status: TaskStatus
    progress: int = 0
    result: Any = None
    error: str | None = None
    meta: dict[str, Any] | None = None


def get_task_info(task_id: str) -> TaskInfo:
    """查询 Celery 任务状态。"""
    result = celery_app.AsyncResult(task_id)

    status = TaskStatus(result.status) if result.status in TaskStatus.__members__ else TaskStatus.PENDING
    meta = result.info if isinstance(result.info, dict) else {}
    progress = meta.get("progress", 0) if status not in (TaskStatus.SUCCESS, TaskStatus.FAILURE) else 100

    error = None
    if status == TaskStatus.FAILURE:
        error = str(result.info) if result.info else "Unknown error"
        progress = 0

    return TaskInfo(
        task_id=task_id,
        status=status,
        progress=progress,
        result=result.result if status == TaskStatus.SUCCESS else None,
        error=error,
        meta=meta,
    )


def revoke_task(task_id: str, terminate: bool = False) -> bool:
    """撤销任务。terminate=True 时强制终止正在执行的任务。"""
    celery_app.control.revoke(task_id, terminate=terminate)
    return True
