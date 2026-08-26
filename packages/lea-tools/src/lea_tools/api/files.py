"""沙箱文件端点 — NFS readdir（list）+ S3 下载（download，兼容旧 session）。

v2 架构：workspace 挂 NFS host-mount，文件直接 readdir 即可。
S3 下载保留给冷层归档恢复后的兼容场景。

鉴权：本服务是内部服务，不做 JWT。session 归属校验在 lea-agent 反代层完成。
"""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from lea_tools.config import get_s3_settings
from lea_tools.services.artifact_pusher import artifact_key
from lea_tools.services.file_store import (
    SKILL_MOUNT,
    Stat,
    StaleWriteError,
    get_file_store,
)

router = APIRouter(prefix="/api/files", tags=["files"])

def _validate_session_id(session_id: str) -> str:
    if not session_id or len(session_id) < 6:
        raise HTTPException(400, "session_id too short")
    if not all(c.isalnum() or c in "-_" for c in session_id):
        raise HTTPException(400, "bad session_id chars")
    return session_id


def _validate_tenant_id(tenant_id: str) -> str:
    if not tenant_id or len(tenant_id) < 4:
        raise HTTPException(400, "tenant_id too short")
    if not all(c.isalnum() or c in "-_" for c in tenant_id):
        raise HTTPException(400, "bad tenant_id chars")
    return tenant_id


@router.get("/list")
def list_files(
    session_id: str = Query(...),
    tenant_id: str = Query("", description="租户 ID；空则只能走 sandbox store"),
    show_hidden: bool = Query(False),
):
    """列 workspace 文件树。store 由 FILE_STORE 决定（nfs / sandbox / auto）。"""
    session_id = _validate_session_id(session_id)
    if tenant_id:
        tenant_id = _validate_tenant_id(tenant_id)

    store = get_file_store(tenant_id, session_id)
    if store is None:
        return {"files": [], "truncated": False, "store": "none"}

    try:
        entries, truncated = store.list(show_hidden=show_hidden)
    except Exception as e:
        raise HTTPException(502, f"{store.kind} store list failed: {e}")

    return {
        "files": [e.as_dict() for e in entries],
        "truncated": truncated,
        "store": store.kind,
    }


# ── Agent 文件工具端点（read / write / edit）─────────────────────
#
# 供 lea-agent 的服务端内置文件工具调用。跟 /list、/download 的区别是这三个是
# agent 在 turn 中间调的，不是用户点界面触发的 —— 所以错误信息要写给模型看：
# 它读完这句话得知道下一步该干什么（重读？换文件名？加上下文？）。
#
# 单文件大小上限。模型读一个超大文件除了撑爆上下文没别的作用，写也一样。
_MAX_AGENT_FILE_MB = 10


def _agent_store(tenant_id: str, session_id: str):
    """解析 store，并要求 tenant_id —— 写操作不能像 /list 那样容忍空租户。

    /list 允许空 tenant_id（回落 sandbox store），但那是只读的。写侧若允许空租户，
    NFS store 会拿不到 workspace 路径而静默回落到沙箱，两次调用可能落在不同后端上，
    新鲜度校验就失去意义了。
    """
    _validate_session_id(session_id)
    _validate_tenant_id(tenant_id)
    store = get_file_store(tenant_id, session_id)
    if store is None:
        raise HTTPException(503, "workspace 不可用：session 可能已归档或沙箱未启动")
    return store


def _guard_size(data: bytes) -> None:
    limit = _MAX_AGENT_FILE_MB * 1024 * 1024
    if len(data) > limit:
        raise HTTPException(413, f"文件超过 {_MAX_AGENT_FILE_MB}MB 上限，请分块处理或用 code_interpreter")


@router.get("/read")
def read_file(
    session_id: str = Query(...),
    tenant_id: str = Query(...),
    path: str = Query(..., description="workspace 内相对路径"),
):
    """读一个文件，连同 (size, mtime_ns) 一起返回。

    调用方必须记住这两个值：写回同一文件时要带上，服务端用它判断"你读完之后有没有
    别人改过"。这是防丢失更新的关键 —— 见 file_store 模块文档。
    """
    store = _agent_store(tenant_id, session_id)
    # 越界检查抛的 PermissionError 和文件系统抛的 EACCES 是两件完全不同的事，必须分开
    # 报。这里原先一个 `except PermissionError` 全接了，都翻译成"路径越界"——于是一个
    # 「文件权限不对」的问题显示为「路径穿越」，实测把排查带偏了好几轮：同一目录下不
    # 存在的文件报 404、存在的文件报 403，看起来像路径映射有 bug，实际是 read_bytes()
    # 拿到了 EACCES。
    #
    # 区分办法是让 store 的越界检查抛带固定前缀的消息（"path escapes ..."），OS 的
    # EACCES 不会长这样。
    try:
        data = store.read(path)
    except PermissionError as e:
        if "escapes" in str(e):
            raise HTTPException(403, f"路径越界：{path} 不在允许的目录内")
        raise HTTPException(
            403,
            f"没有读取权限：{path}（文件系统拒绝访问，通常是投递时的 owner/mode 不对，"
            f"不是路径问题）",
        )
    except FileNotFoundError:
        raise HTTPException(404, f"文件不存在：{path}")
    except Exception as e:
        raise HTTPException(502, f"{store.kind} store read failed: {e}")

    # stat 走的是严格的 _resolve，skill 挂载点在那里必然越界 —— 所以对它不取 stat。
    # 新鲜度校验只对 workspace 里可写的文件有意义，skill 目录是只读的平台资源。
    st = None
    if not path.strip().startswith(SKILL_MOUNT):
        try:
            st = store.stat(path)
        except Exception:
            st = None

    _guard_size(data)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            415,
            f"{path} 不是 UTF-8 文本文件（{len(data)} 字节）。"
            f"二进制文件请用 code_interpreter 处理，或用 /api/files/download 取原始内容。",
        )
    return {
        "path": path,
        "content": text,
        "stat": st.as_dict() if st else None,
        "store": store.kind,
    }


class WriteRequest(BaseModel):
    session_id: str
    tenant_id: str
    path: str
    content: str
    # 你读到的那一版的标识。覆盖已存在的文件时必填 —— 不填即视为盲写，拒绝。
    expect_mtime_ns: int | None = None
    expect_size: int | None = None
    # True = 只允许创建新文件，目标已存在就报错。
    create_only: bool = False


@router.post("/write")
def write_file(body: WriteRequest):
    """整文件写入（原子替换）。覆盖已存在的文件需通过新鲜度校验。"""
    store = _agent_store(body.tenant_id, body.session_id)
    data = body.content.encode("utf-8")
    _guard_size(data)

    expect = None
    if body.expect_mtime_ns is not None:
        expect = Stat(size=body.expect_size or 0, mtime_ns=body.expect_mtime_ns)

    try:
        st = store.write(body.path, data, expect=expect, allow_create_only=body.create_only)
    except PermissionError:
        raise HTTPException(403, f"路径越界：{body.path} 不在 workspace 内")
    except StaleWriteError as e:
        # 409 而不是 400：这不是请求格式错，是并发冲突，重读后重试就能成功。
        raise HTTPException(409, str(e))
    except Exception as e:
        raise HTTPException(502, f"{store.kind} store write failed: {e}")

    return {"path": body.path, "bytes_written": len(data), "stat": st.as_dict(), "store": store.kind}


class SkillProvisionRequest(BaseModel):
    session_id: str
    tenant_id: str
    # [{"name": "pptx-generator", "bundle_uri": "s3://..."}]
    skills: list[dict]


@router.post("/skills/provision")
def provision_skills(body: SkillProvisionRequest):
    """把 agent 绑定的 skill 投递到 session 的 skills 目录。

    调用方是 lea-agent 的会话创建路径。在此之前投递只发生在**沙箱创建那一刻**
    （sandbox_session.py 的 create()），于是 skill 文件在盘上的时机取决于模型有没有
    恰好先调过一次 code_interpreter —— Read/Glob 这类不进沙箱的工具因此读不到
    skills/，报 FileNotFoundError。把投递绑到会话生命周期上就没有这个隐式依赖。

    幂等：ensure_cached 按 bundle 的 sha 复用 `{root}/cache/skills/<sha>/`，
    所以重复调用是本地目录拷贝，不重新下 S3。沙箱创建时那次投递保留不动 ——
    兜住没走这条新路径的调用方，多投一次无害。

    整体失败不抛 5xx：读不到 skill 文件是降级（模型少一份参考），让会话建不起来
    是彻底不可用。失败的 skill 名字在 provisioned 里缺席，调用方可据此告警。
    """
    session_id = _validate_session_id(body.session_id)
    tenant_id = _validate_tenant_id(body.tenant_id)

    if not body.skills:
        return {"provisioned": [], "requested": 0}

    from lea_tools.services.skill_provisioner import provision_session_skills

    try:
        done = provision_session_skills(tenant_id, session_id, body.skills)
    except Exception as e:
        # provision_session_skills 内部已按 skill 逐个 try，能到这里的是
        # ensure_session_dir 级别的故障（共享存储不可写）。
        raise HTTPException(502, f"skill provision failed: {e}")

    return {"provisioned": done, "requested": len(body.skills)}


class EditRequest(BaseModel):
    session_id: str
    tenant_id: str
    path: str
    old_string: str
    new_string: str
    expect_mtime_ns: int | None = None
    expect_size: int | None = None


@router.post("/edit")
def edit_file(body: EditRequest):
    """串替换。要求 old_string 在文件里**恰好出现一次**。

    0 次和多次都报错而不是猜：静默改第一处是最难发现的一类错误 —— 调用方以为改对了，
    实际改的是另一处，而且没有任何信号。报错信息告诉模型怎么让匹配唯一。
    """
    store = _agent_store(body.tenant_id, body.session_id)
    if not body.old_string:
        raise HTTPException(400, "old_string 不能为空；创建新文件请用 /write")

    expect = None
    if body.expect_mtime_ns is not None:
        expect = Stat(size=body.expect_size or 0, mtime_ns=body.expect_mtime_ns)

    # 读-改-写整段交给 store.edit —— 它在一把路径锁内完成。放在这里自己
    # read 再 write 的话，两个并发 edit 会都基于同一版，后者静默抹掉前者。
    try:
        st, replaced = store.edit(body.path, body.old_string, body.new_string, expect=expect)
    except PermissionError:
        raise HTTPException(403, f"路径越界：{body.path} 不在 workspace 内")
    except FileNotFoundError:
        raise HTTPException(404, f"文件不存在：{body.path}。若要新建请用 /write")
    except StaleWriteError as e:
        raise HTTPException(409, str(e))
    except UnicodeDecodeError:
        raise HTTPException(415, f"{body.path} 不是 UTF-8 文本文件，无法做串替换")
    except ValueError as e:
        # store.edit 用 ValueError(次数) 表示匹配数不为 1
        count = str(e)
        if count == "0":
            raise HTTPException(
                422,
                f"在 {body.path} 里找不到 old_string。"
                f"注意空白和缩进必须完全一致；建议先读该文件确认原文。",
            )
        raise HTTPException(
            422,
            f"old_string 在 {body.path} 里出现 {count} 次，无法确定改哪一处。"
            f"请在 old_string 前后多带几行上下文，使其唯一。",
        )
    except Exception as e:
        raise HTTPException(502, f"{store.kind} store edit failed: {e}")

    return {
        "path": body.path,
        "replaced": replaced,
        "bytes_delta": len(body.new_string) - len(body.old_string),
        "stat": st.as_dict(),
        "store": store.kind,
    }


# 单个产物下载大小硬上限。超过就 413（Payload Too Large）拒绝。
_MAX_ARTIFACT_MB_DEFAULT = 500


def _s3_client():
    s = get_s3_settings()
    endpoint = s.internal
    if not endpoint or not s.access_key:
        raise HTTPException(503, "S3 not configured")
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=s.access_key,
        aws_secret_access_key=s.secret_key,
        region_name=s.region,
        config=BotoConfig(
            signature_version="s3v4",
            s3={"addressing_style": "path" if s.use_path_style else "virtual"},
        ),
    )


def _safe_key(session_id: str, path: str) -> str:
    """把用户传的相对路径拼成 S3 key，拒绝越权。"""
    if not path or path.startswith("/") or "\x00" in path:
        raise HTTPException(400, "bad path")

    key = artifact_key(session_id, path)
    prefix = f"sessions/{session_id}"
    normalized = os.path.normpath(key).replace("\\", "/")
    if not normalized.startswith(prefix + "/"):
        raise HTTPException(400, "path escapes session")
    return normalized


@router.get("/download")
def download(
    session_id: str = Query(..., description="沙箱 session_id"),
    path: str = Query(..., description="相对 /workspace 的产物路径，如 report.xlsx"),
    tenant_id: str = Query("", description="租户 ID（有值则走 NFS 直读）"),
):
    session_id = _validate_session_id(session_id)
    if tenant_id:
        tenant_id = _validate_tenant_id(tenant_id)
    if not path or path.startswith("/") or "\x00" in path:
        raise HTTPException(400, "bad path")

    # store 直读优先（NFS 或 envd）
    store = get_file_store(tenant_id, session_id)
    if store is not None:
        try:
            data = store.read(path)
        except PermissionError:
            raise HTTPException(400, "path escapes workspace")
        except FileNotFoundError:
            data = None
        except Exception as e:
            raise HTTPException(502, f"{store.kind} store read failed: {e}")

        if data is not None:
            filename = os.path.basename(path)
            media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            return StreamingResponse(
                iter([data]),
                media_type=media_type,
                headers={
                    "Content-Disposition":
                        f"attachment; filename*=UTF-8''{quote(filename)}",
                    "Content-Length": str(len(data)),
                    "X-File-Store": store.kind,
                },
            )

    # S3 fallback（老 session / 归档恢复场景）
    key = _safe_key(session_id, path)

    s3 = _s3_client()
    bucket = get_s3_settings().bucket

    max_mb = int(os.environ.get("MAX_ARTIFACT_MB", _MAX_ARTIFACT_MB_DEFAULT))
    max_bytes = max_mb * 1024 * 1024

    # 先 HEAD 拿大小，超大文件提前 413 而不是拉了半天再切
    try:
        meta = s3.head_object(Bucket=bucket, Key=key)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "NotFound"):
            raise HTTPException(404, f"file not found: {path}")
        raise HTTPException(502, f"s3 error: {code or e}")

    size = meta.get("ContentLength", 0)
    if size > max_bytes:
        raise HTTPException(
            413,
            f"file too large: {size} bytes > {max_bytes} (MAX_ARTIFACT_MB={max_mb})",
        )

    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "NotFound"):
            raise HTTPException(404, f"file not found: {path}")
        raise HTTPException(502, f"s3 error: {code or e}")

    filename = os.path.basename(path)
    media_type = (
        meta.get("ContentType")
        or mimetypes.guess_type(filename)[0]
        or "application/octet-stream"
    )

    def _stream() -> Iterator[bytes]:
        try:
            for chunk in obj["Body"].iter_chunks(chunk_size=64 * 1024):
                yield chunk
        finally:
            obj["Body"].close()

    # RFC 5987 encoding — 支持中文文件名不乱码
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    headers = {"Content-Disposition": disposition}
    length = obj.get("ContentLength")
    if length is not None:
        headers["Content-Length"] = str(length)

    return StreamingResponse(_stream(), media_type=media_type, headers=headers)
