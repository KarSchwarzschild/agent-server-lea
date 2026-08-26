"""核心解析 API — parse / chunk / parse-and-chunk。"""

from __future__ import annotations

import os
import tempfile
import time
import uuid

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from lea_parser.auth import CurrentUser, get_current_user
from lea_parser.chunking import chunk_recursive, chunk_sentence_window
from lea_parser.config import get_parser_settings
from lea_parser.database import get_db
from lea_parser.models.task import ParseTask
from lea_parser.parsers import parse_document
from lea_parser.parsers.dispatcher import SUPPORTED_FORMATS
from lea_parser.services.quota import (
    QuotaError, check_file_size, check_rate_limit, record_usage,
)

logger = structlog.get_logger()
router = APIRouter(tags=["parse"])


class ChunkRequest(BaseModel):
    text: str
    strategy: str = "sentence_window"
    chunk_size: int = 200
    chunk_overlap: int = 30


class ChunkResponse(BaseModel):
    chunks: list[dict]
    total: int


@router.post("/parse")
def parse_file(
    file: UploadFile = File(...),
    chunk: bool = Query(True, description="是否同时分块"),
    strategy: str = Query("sentence_window"),
    chunk_size: int = Query(200),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """解析上传文件，返回结构化内容。"""
    settings = get_parser_settings()
    task_id = str(uuid.uuid4())
    start = time.time()

    try:
        check_rate_limit(user.tenant_id, user.user_id, db)
    except QuotaError as e:
        raise HTTPException(429, detail=e.message)

    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in SUPPORTED_FORMATS:
        raise HTTPException(400, f"Unsupported format: {ext}")

    content = file.file.read()
    file_size = len(content)

    if file_size > settings.max_file_size_mb * 1024 * 1024:
        raise HTTPException(413, "File too large")

    try:
        check_file_size(user.tenant_id, user.user_id, file_size, db)
    except QuotaError as e:
        raise HTTPException(413, detail=e.message)

    suffix = f".{ext}" if ext else ""
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        result = parse_document(tmp_path)

        if chunk and result.get("text_blocks"):
            if strategy == "recursive":
                md = result.get("md_content", "")
                result["chunks"] = chunk_recursive(md, chunk_size)
            else:
                result["chunks"] = chunk_sentence_window(
                    result["text_blocks"], chunk_size
                )
        elif chunk and result.get("md_content"):
            result["chunks"] = chunk_recursive(result["md_content"], chunk_size)

        duration_ms = int((time.time() - start) * 1000)
        chunks_count = len(result.get("chunks", []))

        record_usage(
            db, user.tenant_id, user.user_id, task_id,
            file.filename or "", ext, file_size, chunks_count, duration_ms,
        )

        return {
            "task_id": task_id,
            "filename": file.filename,
            "format": ext,
            "file_size": file_size,
            "md_content": result.get("md_content", ""),
            "chunks": result.get("chunks", []),
            "image_items": result.get("image_items", []),
            "duration_ms": duration_ms,
        }
    finally:
        os.unlink(tmp_path)


@router.post("/chunk", response_model=ChunkResponse)
def chunk_text(
    req: ChunkRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """对纯文本做分块（不含文件解析）。"""
    if req.strategy == "recursive":
        chunks = chunk_recursive(req.text, req.chunk_size, req.chunk_overlap)
    else:
        blocks = [{"text": req.text, "page_idx": 0}]
        chunks = chunk_sentence_window(blocks, req.chunk_size)
    return {"chunks": chunks, "total": len(chunks)}


@router.get("/formats")
def list_formats():
    """返回支持的文件格式列表。"""
    return {"formats": sorted(SUPPORTED_FORMATS)}
