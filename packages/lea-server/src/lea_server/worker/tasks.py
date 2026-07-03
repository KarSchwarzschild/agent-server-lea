"""
注册的异步任务。

每个任务都是独立的、可重试的工作单元。
任务内通过 self.update_state() 上报进度，供 SSE 推送给前端。
"""

from __future__ import annotations

import structlog
from celery import Task

from lea_server.worker.celery_app import celery_app

logger = structlog.get_logger()


class LeaTask(Task):
    """自定义 Task 基类 — 统一错误处理和日志。"""

    autoretry_for = (TimeoutError, ConnectionError)
    retry_backoff = True
    retry_backoff_max = 300
    max_retries = 3

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error("task_failed", task_id=task_id, task_name=self.name, error=str(exc))

    def on_success(self, retval, task_id, args, kwargs):
        logger.info("task_succeeded", task_id=task_id, task_name=self.name)


@celery_app.task(base=LeaTask, bind=True, name="lea_server.worker.tasks.generate_review")
def generate_review(self, tenant_id: str, task_id: str, topic: str, config: dict | None = None):
    """综述生成任务 — 调用 Research App 的 LangGraph 管道。

    通过 self.update_state() 上报各阶段进度：
    - SEARCHING: 文献检索中
    - ANALYZING: 文献分析中
    - GENERATING: 综述生成中
    - COMPLETED: 完成
    """
    self.update_state(state="SEARCHING", meta={"progress": 10, "stage": "search"})
    logger.info("review_task_started", task_id=task_id, topic=topic, tenant_id=tenant_id)

    # TODO: Phase 4 实现时接入 Research App 的 review_graph
    self.update_state(state="COMPLETED", meta={"progress": 100, "stage": "done"})
    return {"task_id": task_id, "status": "completed"}


@celery_app.task(base=LeaTask, bind=True, name="lea_server.worker.tasks.batch_import")
def batch_import(self, tenant_id: str, file_paths: list[str]):
    """批量文档导入 — 解析 PDF、构建索引、存入向量库。"""
    total = len(file_paths)
    for i, path in enumerate(file_paths):
        self.update_state(
            state="IMPORTING",
            meta={"progress": int((i + 1) / total * 100), "current_file": path},
        )
        logger.info("importing_file", file=path, progress=f"{i+1}/{total}")
        # TODO: Phase 2 实现时接入 lea-rag 的 IngestionPipeline

    return {"imported": total}


@celery_app.task(base=LeaTask, bind=True, name="lea_server.worker.tasks.run_evaluation")
def run_evaluation(self, tenant_id: str, dataset_id: str, suite: str = "all"):
    """评测任务 — 执行 Harness 评测套件。"""
    self.update_state(state="EVALUATING", meta={"progress": 0, "suite": suite})
    logger.info("evaluation_started", dataset_id=dataset_id, suite=suite)

    # TODO: Phase 1.10 实现时接入 Harness
    return {"dataset_id": dataset_id, "suite": suite, "status": "completed"}
