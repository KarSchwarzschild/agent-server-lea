"""Artifact 导出 API — 将 slides artifact 导出为 PPTX/ZIP。"""

from __future__ import annotations

import json

import structlog
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

router = APIRouter(prefix="/api/chat/artifacts", tags=["artifacts"])
logger = structlog.get_logger(__name__)


@router.post("/{artifact_id}/export")
async def export_artifact(
    artifact_id: str,
    format: str = Query(default="pptx", description="导出格式: pptx / zip"),
):
    """导出 slides artifact 为可下载文件。"""
    from lea_server.services.object_storage import get_object_storage

    storage = get_object_storage()

    # 在 MinIO 中查找 artifact（按 artifact_id 前缀搜索）
    # 简化实现：遍历已知路径模式
    from lea_graph.skills.pipelines.slide_export import (
        export_slides_as_pptx,
        export_slides_as_zip,
    )

    # 尝试加载 artifact JSON
    # artifact 存储路径: artifacts/{session_id}/{artifact_id}.json
    # 由于我们只有 artifact_id，需要从前缀搜索
    try:
        # 用 list_objects 搜索（boto3 方式）
        artifact_data = _find_artifact(storage, artifact_id)
    except Exception as e:
        logger.error("artifact_not_found", artifact_id=artifact_id, error=str(e))
        raise HTTPException(status_code=404, detail="Artifact not found")

    slides = artifact_data.get("slides", [])
    if not slides:
        raise HTTPException(status_code=400, detail="No slides in artifact")

    title = "slides"
    outline = artifact_data.get("outline", [])
    if outline and outline[0].get("title"):
        title = outline[0]["title"]

    if format == "zip":
        file_bytes, filename = await export_slides_as_zip(slides, title)
        media_type = "application/zip"
    else:
        file_bytes, filename = await export_slides_as_pptx(slides, title)
        media_type = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

    return Response(
        content=file_bytes,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _find_artifact(storage, artifact_id: str) -> dict:
    """在 MinIO 中搜索 artifact JSON。"""
    # 用 boto3 list_objects_v2 按前缀搜索
    prefix = "artifacts/"
    client = storage._client
    bucket = storage._bucket

    resp = client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=500)
    for obj in resp.get("Contents", []):
        key = obj["Key"]
        if artifact_id in key and key.endswith(".json"):
            data = storage.get_object(key)
            return json.loads(data)

    raise FileNotFoundError(f"Artifact {artifact_id} not found")
