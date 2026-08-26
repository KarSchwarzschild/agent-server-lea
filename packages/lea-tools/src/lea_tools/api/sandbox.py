"""Sandbox Session API — Go Agent 通过这组接口管理沙箱。"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from lea_tools.services._cube_sandbox import SandboxLookupError
from lea_tools.services.sandbox_session import get_sandbox_manager

router = APIRouter(prefix="/sandbox/sessions", tags=["sandbox"])

# 沙箱住在 Cube 里，本进程只缓存句柄。缓存没命中时会去 Cube 认领（见
# SandboxSessionManager._adopt），于是这里有两种"失败"必须分开报：
#
#   404  Cube 说了：这个会话没有沙箱。客户端该重新建。
#   503  问不到 Cube。**不知道**有没有。客户端该重试，绝不能去建新的 ——
#        那会在 Cube 恢复之后留下一个没人认领的孤儿沙箱，占着 slot 不放。
#
# 以前这两种都表现为 404（其实连第一种都到不了，缓存没命中就直接 404 了）。
_NOT_FOUND = "sandbox session not found"
_UNAVAILABLE = "cannot reach sandbox service"


class SkillRef(BaseModel):
    """agent 绑定的一个 skill —— 只要 name（目录名）+ bundle_uri（S3 位置）。"""

    name: str
    bundle_uri: str = ""


class CreateRequest(BaseModel):
    session_id: str
    skill_id: str | None = None
    user_id: str = ""
    tenant_id: str = ""
    # 该 session 所属 agent 绑定的 skill。只有列在这里的会被投递进沙箱 ——
    # 没绑定的 skill 沙箱里根本不存在（见 skill_provisioner 模块注释）。
    skills: list[SkillRef] = []


class CreateResponse(BaseModel):
    sandbox_session_id: str
    sandbox_id: str


@router.post("", response_model=CreateResponse)
async def create_session(req: CreateRequest):
    mgr = get_sandbox_manager()
    try:
        sess = await mgr.create(
            session_id=req.session_id,
            skill_id=req.skill_id,
            user_id=req.user_id,
            tenant_id=req.tenant_id,
            skills=[s.model_dump() for s in req.skills],
        )
    except SandboxLookupError:
        # 认领这一步失败了就必须停下。硬着头皮建的话，等 Cube 恢复会发现同一个会话
        # 有两个沙箱 —— 而且是**两个都在跑**，谁都不会被回收。
        raise HTTPException(503, _UNAVAILABLE)
    return CreateResponse(
        sandbox_session_id=sess.session_id,
        sandbox_id=sess.sandbox_id,
    )


class ExecRequest(BaseModel):
    code: str
    language: str = "python"
    timeout: int = 60


@router.post("/{session_id}/exec")
async def exec_code(session_id: str, req: ExecRequest):
    mgr = get_sandbox_manager()
    try:
        result = await mgr.execute(
            session_id, req.code, req.language, req.timeout
        )
    except SandboxLookupError:
        raise HTTPException(503, _UNAVAILABLE)
    except KeyError:
        raise HTTPException(404, _NOT_FOUND)
    return result


class UploadRequest(BaseModel):
    path: str
    data_base64: str


@router.post("/{session_id}/upload")
async def upload_file(session_id: str, req: UploadRequest):
    import base64
    mgr = get_sandbox_manager()
    try:
        await mgr.upload_file(
            session_id, req.path, base64.b64decode(req.data_base64)
        )
    except SandboxLookupError:
        raise HTTPException(503, _UNAVAILABLE)
    except KeyError:
        raise HTTPException(404, _NOT_FOUND)
    return {"status": "ok"}


@router.get("/{session_id}/download")
async def download_file(session_id: str, path: str):
    import base64
    mgr = get_sandbox_manager()
    try:
        data = await mgr.download_file(session_id, path)
    except SandboxLookupError:
        raise HTTPException(503, _UNAVAILABLE)
    except KeyError:
        raise HTTPException(404, _NOT_FOUND)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, f"file not found: {path}")
    return {"path": path, "data_base64": base64.b64encode(data).decode()}


@router.delete("/{session_id}")
async def destroy_session(session_id: str):
    mgr = get_sandbox_manager()
    await mgr.destroy(session_id)
    return {"status": "ok"}
