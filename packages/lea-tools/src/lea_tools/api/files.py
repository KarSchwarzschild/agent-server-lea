"""沙箱文件端点 — NFS readdir（list）+ S3 下载（download，兼容旧 session）。

v2 架构：workspace 挂 NFS host-mount，文件直接 readdir 即可。
S3 下载保留给冷层归档恢复后的兼容场景。

鉴权：本服务是内部服务，不做 JWT。session 归属校验在 lea-agent 反代层完成。
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from lea_tools.config import get_nfs_settings, get_s3_settings
from lea_tools.services.artifact_pusher import artifact_key
from lea_tools.services.file_stager import (
    STAGEABLE_PREFIXES,
    FileStager,
    QuotaExceeded,
    safe_stage_name,
)
from lea_tools.services.file_store import (
    SKILL_MOUNT,
    UPLOADS_MOUNT,
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


def _validate_blob_key(file_key: str, tenant_id: str = "") -> str:
    """校验一个 OBS key 长成 `<uploads|library>/<tenant>/<sha>.<ext>`。

    ## 这是安全边界，不是格式检查

    本服务不做 JWT（见模块头），而拿到 key 就去 OBS 拉字节的端点有两个
    （/uploads/stage 物化进会话目录、GET /uploads/blob 直接回字节）。同一个 bucket 里
    还住着沙箱的 volume 对象、别的租户的上传件、以及全部交付物。

    /uploads/stage 原来只查了 `".." in key` —— 也就是**任意 bucket 内对象都能被物化进
    任意会话的 uploads/ 目录**，然后用 Read 读出来。`..` 拦的是路径穿越，而这里根本
    不需要穿越：`library/<别人的租户>/<sha>.pptx` 是一个完全合法的字符串。

    两道判据：

        prefix   只认 uploads/ 和 library/ —— 其余（沙箱 volume 等）一律拒
        tenant   传了 tenant_id 就要求 key 的第二段跟它相等

    tenant 那道是可选的，因为两个调用点的信息量不同：/uploads/stage 的调用方
    （lea-agent 反代）手里有 JWT 里的 tenant，必须传；GET /uploads/blob 是按
    attachment_id 查库之后才拿到 key 的，归属已经在库那一层校验过（SQL 里带
    tenant+user 过滤），这里再要求一次反而会让"租户 id 改名"之类的历史数据读不出来。
    """
    if not file_key or ".." in file_key or "\x00" in file_key:
        raise HTTPException(400, "bad file_key")
    parts = file_key.split("/")
    if len(parts) < 3 or parts[0] not in STAGEABLE_PREFIXES or not parts[1]:
        raise HTTPException(400, f"bad file_key：只接受 {'/ '.join(STAGEABLE_PREFIXES)}/<tenant>/<sha>")
    if tenant_id and parts[1] != tenant_id:
        # 403 而不是 404：这不是"找不到"，是"这不是你的"。而且不区分的话，调用方
        # （我们自己的反代）拿到 404 会去重试或者当成对象过期，两个都不对。
        raise HTTPException(403, "file_key 不属于该租户")
    return file_key


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


def _quota_507(e: QuotaExceeded) -> HTTPException:
    """把配额异常翻成 507 + 一句模型能照着行动的话。

    三条写路径（write / edit / 物化）共用这一句：用户看到的应该是同一个事实
    （"这个会话的文件配额满了"），不该因为是谁写的而措辞不同。

    ⚠️ 措辞里必须带"删掉不再需要的文件"—— 507 本身只说"存储不足"，模型读到会倾向于
    重试（它对基础设施故障的默认反应），而重试一定再失败。指一条出路，它会去列目录
    然后删中间产物。
    """
    return HTTPException(
        507,
        f"会话文件配额已满（已用 {e.used // 1048576}MB，本次需要 {e.incoming // 1048576}MB，"
        f"上限 {e.limit // 1048576}MB）。删掉工作区里不再需要的中间文件后重试；"
        f"要长期保留的成品请用 deliver_artifact 交付，它会存进用户的文件库。",
    )


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

    # stat 走的是严格的 _resolve，两个 RO 挂载点在那里必然越界 —— 所以对它们不取 stat。
    # 新鲜度校验只对 workspace 里**可写**的文件有意义：
    #   /opt/skills   平台投递的资源，只读
    #   /mnt/uploads  用户上传的原件，只读（改动要写到 /workspace）
    #
    # 不显式跳过也不会报错（下面 except Exception 会吞掉），但那是"靠异常实现逻辑"——
    # 加 uploads 那一轮就是这么漏过去的：读上传件时白跑一次注定失败的 stat。
    st = None
    raw_path = path.strip()
    if not any(raw_path.startswith(m) for m in (SKILL_MOUNT, UPLOADS_MOUNT)):
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
    except QuotaExceeded as e:
        raise _quota_507(e)
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
    except QuotaExceeded as e:
        raise _quota_507(e)
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


# ── 用户上传原件：OBS 落地 + 物化进会话 uploads/ ─────────────────
#
# 两个端点配合完成"上传的文件能被沙箱里的 python 打开"：
#
#     /uploads/blob    收字节 → 算 sha → PUT OBS       （长期真源）
#     /uploads/stage   OBS → NFS sessions/<sid>/uploads （RO 挂进 /mnt/uploads）
#
# 分两步而不是一个端点做完，是因为**这两件事的失败该有不同的后果**：blob 失败意味着
# 文件彻底没保住（要让用户重传）；物化失败只意味着这一个会话里不能改它，问答照常
# （文本已经抽出来了）。合成一个端点的话，物化失败会把整次上传变成失败。
#
# 字节流只穿过 lea-agent 一次（进 /uploads/blob）。物化那步 lea-tools 自己从 OBS 拉，
# 调用方只传 key —— 所以 30MB 的文件不会在两个服务之间走第二遍，也不需要 base64。


class StageRequest(BaseModel):
    tenant_id: str
    session_id: str
    # OBS key，来自 /uploads/blob 的返回
    file_key: str
    # 物化后的文件名。**已经有 staged_path 的调用方原样传它**（上传件：blob 那步
    # 已经算过一次并落了库），这样物化出来的名字跟库里那个值一定相同 —— 那个值会
    # 拼成路径注入给模型，不一致的症状是模型拿到一个不存在的路径。
    #
    # 留空则由本端点用 safe_stage_name(sha, filename) 现算，并在响应的 staged_name
    # 里回给调用方。给文件库导入用：成品行没有 staged_path，此前也从没物化过，
    # 所以没有"必须对齐的既有值"。
    #
    # ⚠️ 两条路都只有**一份**清洗实现（lea-tools 的 safe_stage_name）。调用方要么
    # 传一个本来就是它算出来的值，要么接收本端点算出来的值 —— 任何一边自己重新拼
    # 一遍都会在超长中文名截断之类的边缘情况上分叉。
    dest_name: str = ""
    # dest_name 为空时用来算名字。sha 取前 8 位做前缀，防同名不同内容互相覆盖。
    filename: str = ""
    sha256: str = ""


@router.post("/uploads/blob")
async def put_upload_blob(
    file: UploadFile = File(...),
    tenant_id: str = Form(...),
    sha256: str = Form("", description="调用方算好的 sha256；留空则这里算"),
):
    """把用户上传的原件字节写进 OBS，返回 key + sha256。

    sha 允许调用方传进来（它为了算配额和缓存键本来就要算一次），留空则这里算。
    传了的话会**校验**——对不上说明传输中出了问题，宁可报错也不能让 key 和内容不符：
    key 是内容寻址的，不符就意味着以后按 sha 命中缓存会拿到别的文件的解析结果。
    """
    tenant_id = _validate_tenant_id(tenant_id)
    data = await file.read()
    if not data:
        raise HTTPException(400, "空文件")

    actual = hashlib.sha256(data).hexdigest()
    if sha256 and sha256.lower() != actual:
        raise HTTPException(
            400,
            f"sha256 不匹配（声称 {sha256[:12]}…，实际 {actual[:12]}…）—— 传输可能被截断",
        )

    filename = file.filename or "file"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    stager = FileStager()
    try:
        key = stager.put_blob(tenant_id, actual, ext, data, filename=filename)
    except RuntimeError as e:
        # S3 没配。503 而不是 500：这是部署问题，调用方重试无用但运维能修。
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(502, f"OBS 写入失败：{e}")

    return {
        "file_key": key,
        "sha256": actual,
        "size": len(data),
        "staged_name": safe_stage_name(actual, filename),
    }


@router.post("/uploads/stage")
def stage_upload(body: StageRequest):
    """把 OBS 上的原件物化到会话 uploads/ 目录，让沙箱能打开它。

    配额在这里生效（传给 stage，由它在下载**之前**判）—— 不在上传端点判，因为会话目录
    的占用还有模型自己写的部分，闸门必须在所有写入路径的汇合处。
    超限返 507，调用方据此退化成"只抽文本不落地"。
    """
    tenant_id = _validate_tenant_id(body.tenant_id)
    session_id = _validate_session_id(body.session_id)

    # 前缀 + 租户双重白名单。原来这里只查 `".." in file_key` —— 见 _validate_blob_key
    # 的注释，那挡不住 `library/<别人的租户>/<sha>.pptx` 这种完全合法的字符串。
    _validate_blob_key(body.file_key, tenant_id)

    dest_name = body.dest_name
    if not dest_name:
        # 文件库导入路径：没有既有的 staged_path 要对齐，现算一个。
        # sha 兜底从 key 的最后一段取（`<prefix>/<tenant>/<sha>.<ext>`）——
        # 上面 _validate_blob_key 已经保证了这个形状。
        sha = body.sha256 or os.path.basename(body.file_key).split(".")[0]
        if not body.filename:
            raise HTTPException(400, "dest_name 和 filename 至少要给一个")
        dest_name = safe_stage_name(sha, body.filename)

    # dest_name 必须是单个文件名 —— 它会被直接拼到 uploads/ 下面。
    # 算出来的那条也过这道：safe_stage_name 保证了它，但校验放在汇合处才不会在
    # 以后多一个调用路径时被绕开。
    if not dest_name or "/" in dest_name or "\\" in dest_name \
            or dest_name.startswith("."):
        raise HTTPException(400, "bad dest_name")

    stager = FileStager()
    quota_bytes = get_nfs_settings().workspace_quota_mb * 1024 * 1024

    try:
        name, size = stager.stage(
            tenant_id, session_id, body.file_key, dest_name,
            quota_bytes=quota_bytes,
        )
    except QuotaExceeded as e:
        # 507 Insufficient Storage。调用方（lea-agent）据此把这个附件降级成"只抽文本"，
        # 并在注入文本里告诉模型"本次会话内不可修改"。
        raise HTTPException(
            507,
            f"会话文件配额已满（已用 {e.used // 1048576}MB，本文件 {e.incoming // 1048576}MB，"
            f"上限 {e.limit // 1048576}MB）",
        )
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except RuntimeError as e:
        # 没共享存储。503：物化在这个部署形态下做不到，但问答路径不受影响。
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(502, f"物化失败：{e}")

    return {
        "staged_name": name,
        "size": size,
        "sandbox_path": f"{UPLOADS_MOUNT}/{name}",
    }


@router.get("/uploads/blob")
def get_upload_blob(
    file_key: str = Query(..., description="OBS key"),
    filename: str = Query("", description="下载时给浏览器的文件名"),
):
    """取原件字节。用户下载自己上传的文件走这条路。

    走 OBS 而不是会话目录：blob 是真源，会话归档后 NFS 那份已经清理掉了，blob 还在。

    **这里不做归属校验** —— 跟本模块其余端点一致，鉴权在 lea-agent 反代层
    （它按 attachment_id 查库确认 tenant/user，再拿 file_key 打这里）。file_key 本身
    不可猜（含 sha256），但那不是安全边界，别依赖它。
    """
    # 前缀白名单从"只有 uploads/"放宽到"uploads/ 或 library/"：交付物下载走同一条路
    # （它跟上传件是同一套内容寻址规则，只差前缀）。**不**放宽到任意前缀，见
    # _validate_blob_key —— bucket 里还有沙箱 volume 对象。
    #
    # 这里不传 tenant_id：归属已经在调用方那一层校过了（按 attachment_id / artifact_id
    # 查库，SQL 里带 tenant+user 过滤）。
    _validate_blob_key(file_key)

    stager = FileStager()
    try:
        data, ctype = stager.fetch_blob(file_key)
    except FileNotFoundError:
        # 410 而不是 404：**对象曾经存在过**，这跟"从来没有过"是不同的事，前端可以据此
        # 显示"文件已被删除"而不是"找不到"。
        #
        # ⚠️ 原来这句话写的是"被 lifecycle 规则清掉了"，而那条规则从来没配过
        # （infra/ 里搜不到任何 OBS 生命周期配置，见 file_stager.upload_key 的注释）。
        # 现在对象消失的真实原因只有一个：用户自己在文件库里删了它
        # （DELETE /api/user/files/...，那条路会同时删对象和登记行）。
        # 所以措辞按"被删除"说，不按"过期"说 —— 后者会让用户等一个不会到来的恢复。
        raise HTTPException(410, "原件已被删除")
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(502, f"OBS 读取失败：{e}")

    name = filename or os.path.basename(file_key)
    return StreamingResponse(
        iter([data]),
        media_type=ctype,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
            "Content-Length": str(len(data)),
        },
    )


@router.delete("/blob")
def delete_blob(file_key: str = Query(..., description="OBS key")):
    """删掉一个 OBS 对象。用户在文件库里删文件时，由 lea-agent 在**删完登记行、
    并确认同租户内再没有别的行引用同一个 sha 之后**调这里。

    ## 引用计数为什么不在这边做

    这个服务不连业务库（见模块头：它不做 JWT，也不查 chat_attachments /
    session_artifacts）。要在这里数引用就得给它一条数据库连接，而那会让"谁是数据的
    权威"变成两个答案。lea-agent 手里已经有库和事务顺序，判定留在它那边。

    这边只保证一件事：**这个 key 长得像我们自己的东西**。没有这道校验的话，
    一个 DELETE 端点加上任意 key 就是"删掉 bucket 里任何对象"——包括别的租户的
    上传件和沙箱的 volume 对象。

    幂等：S3 对不存在的 key 也返 204，所以重复删是安全的（引用计数判完到真删之间
    进程挂掉，重试不会出错）。
    """
    # 不传 tenant_id：跟 GET /uploads/blob 同一条理由 —— 归属已经在调用方按
    # (tenant, user, id) 查库时校过了，这里再要求一次会让租户改名之类的历史数据删不掉。
    _validate_blob_key(file_key)

    stager = FileStager()
    try:
        stager.delete_object(file_key)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        # 502 而不是吞掉：删不掉必须让调用方知道。它那边登记行已经删了，
        # 对象留下就是个孤儿 —— 报出来才有人能去对账。
        raise HTTPException(502, f"OBS 删除失败：{e}")
    return {"status": "ok", "file_key": file_key}


# 单个产物的大小硬上限，交付和下载共用。
#
# 共用一个数是有意的：交付比下载宽的话，模型能成功交付一份没人取得到的成品 ——
# 用户在文件库里看到它、点下载拿到 413，而那是最坏的形状（东西"在"但拿不到）。
_MAX_ARTIFACT_MB_DEFAULT = 500


def _max_artifact_bytes() -> int:
    """环境变量每次现读，不缓存 —— 调这个数是为了应急放开一个卡住的大文件，
    改完得重启进程才生效的话，应急就不成立了。"""
    return int(os.environ.get("MAX_ARTIFACT_MB", _MAX_ARTIFACT_MB_DEFAULT)) * 1024 * 1024


# ── 交付物发布：会话 workspace → OBS library/ ────────────────────
#
# 这是 deliver_artifact 工具的服务端一半。lea-agent 那边负责登记（session_artifacts
# 落一行）和配额判定，这边只负责**把字节从会话工作区搬到长期存储**并算出 sha。
#
# ## 为什么字节不经过 lea-agent
#
# 跟 /uploads/stage 同一个理由的镜像：文件已经在共享存储上了，lea-tools 读得到。
# 让 lea-agent 先 download 再 POST 上来，一份 200MB 的 PPT 要在两个服务之间多走一趟
# 网络和两趟内存，而它一个字节都不需要看。
#
# 所以这个端点收的是**路径**，返回的是 (sha256, obs_key, size) —— 那三个值正是登记
# 那一行需要的，而它们只有摸过字节的人才算得出来。


class PublishRequest(BaseModel):
    tenant_id: str
    session_id: str
    # 会话 workspace 内的相对路径，如 output/report.pptx
    path: str


@router.post("/artifacts/publish")
def publish_artifact(body: PublishRequest):
    """把会话工作区里的一个文件发布到 OBS 的 library/ 前缀。

    幂等：内容寻址，同一份内容重复发布是同一个 key，OBS 那边 head 一下就跳过。
    所以模型改三轮才定稿、每轮都 deliver 的成本是三次 head + 一次 put。

    ## 不判配额

    配额判定在 lea-agent 那一侧（它才知道这个 user 的库里已经占了多少 —— 那个数字
    要查 MySQL）。而且交付超限是**软放行**：行落下来、obs_key 留空、给模型一句降级
    说明。所以这个端点被调到就是要写，写不进去才报错。

    ## 大小上限借用产物下载那个（500MB）

    模型能交付的东西不该比用户能下载的东西大 —— 那样交付成功了但没人取得到。
    """
    tenant_id = _validate_tenant_id(body.tenant_id)
    session_id = _validate_session_id(body.session_id)
    if not body.path or body.path.startswith("/") or "\x00" in body.path:
        raise HTTPException(400, "bad path")

    store = get_file_store(tenant_id, session_id)
    if store is None:
        raise HTTPException(503, "workspace 不可用：session 可能已归档或沙箱未启动")

    try:
        data = store.read(body.path)
    except PermissionError as e:
        if "escapes" in str(e):
            raise HTTPException(403, f"路径越界：{body.path} 不在工作区内")
        raise HTTPException(403, f"没有读取权限：{body.path}")
    except FileNotFoundError:
        # 404 的措辞要能让模型自救：它最可能的错误是把打算写的路径当成已写好的路径
        # 交付了（"我要生成 output/report.pptx" → 直接 deliver，而文件还没落盘）。
        raise HTTPException(
            404,
            f"工作区里没有 {body.path}。确认文件已经写好（先 list 看一下实际路径）"
            f"再交付 —— 交付不会创建文件，它只是把已有的文件登记成成品。",
        )
    except Exception as e:
        raise HTTPException(502, f"{store.kind} store read failed: {e}")

    if not data:
        # 空文件几乎一定是搞错了（写失败、或者交付了一个还没写内容的占位文件），
        # 而它作为"成品"交给用户是最坏的形状 —— 用户下载下来打开是空的。
        raise HTTPException(422, f"{body.path} 是空文件，不能作为成品交付")

    limit = _max_artifact_bytes()
    if len(data) > limit:
        raise HTTPException(413, f"成品 {len(data) // 1048576}MB，超过 {limit // 1048576}MB 上限")

    sha = hashlib.sha256(data).hexdigest()
    filename = os.path.basename(body.path)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    stager = FileStager()
    try:
        key = stager.put_library_object(tenant_id, sha, ext, data, filename=filename)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(502, f"OBS 写入失败：{e}")

    return {
        "obs_key": key,
        "sha256": sha,
        "size": len(data),
        "filename": filename,
        "mime_type": mimetypes.guess_type(filename)[0] or "application/octet-stream",
    }


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

    max_bytes = _max_artifact_bytes()
    max_mb = max_bytes // 1048576

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
