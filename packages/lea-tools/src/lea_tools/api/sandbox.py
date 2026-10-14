"""Sandbox Session API — Go Agent 通过这组接口管理沙箱。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
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
    """创建沙箱会话（幂等操作）。

    基于 session_id 的幂等保证：
    - 如果该 session_id 已有沙箱，直接返回已存在的沙箱信息
    - 如果不存在，创建新沙箱

    对齐行业标准（OpenAI thread_id, E2B sandbox_id）。
    """
    # 幂等检查：先查询 Cube 是否已有该 session 的沙箱
    from lea_tools.services._cube_sandbox import find_session_sandbox
    try:
        existing = await asyncio.to_thread(find_session_sandbox, req.session_id)
        if existing:
            # 已存在，直接返回（幂等）
            import structlog
            log = structlog.get_logger()
            log.info(
                "sandbox already exists, returning existing",
                session_id=req.session_id,
                sandbox_id=existing["sandbox_id"],
            )
            return CreateResponse(
                sandbox_session_id=req.session_id,
                sandbox_id=existing["sandbox_id"],
            )
    except SandboxLookupError:
        # Cube 不可达，必须停止（不能盲目创建）
        raise HTTPException(503, _UNAVAILABLE)

    # 不存在，创建新沙箱
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


@router.get("/{session_id}")
async def get_session_state(session_id: str):
    """问 Cube 这个会话的沙箱现在是什么状态。不碰它，纯只读。

    ## 为什么需要这个端点

    沙箱的生命周期是**三段**的，而中间那段没有任何代码路径：

      0 ~ CUBE_SANDBOX_TIMEOUT      running
      timeout ~ IDLE_TIMEOUT        paused（Cube 按 on_timeout=pause 自己转的）
      IDLE_TIMEOUT 之后             回收器真删

    转 paused 和 auto_resume 都是 Cube 侧的策略，我们一行代码都不写。好处是它不会
    因为我们的进程重启而失效；代价是**它对我们完全不可见** —— 没有这个端点的话，
    "暂停恢复到底有没有在工作"只能靠等下一次调用会不会超时来猜，而那猜不出区别：
    auto_resume 成功和从没暂停过，从调用方看是一样的。

    直接读 Cube 的 state，不缓存、不掺本进程的句柄表 —— 本进程有没有句柄跟沙箱
    活没活着是两件事（多副本下经常不一致），而这里要答的是后者。
    """
    from lea_tools.services._cube_sandbox import find_session_sandbox
    try:
        found = await asyncio.to_thread(find_session_sandbox, session_id)
    except SandboxLookupError:
        raise HTTPException(503, _UNAVAILABLE)
    if not found:
        raise HTTPException(404, _NOT_FOUND)
    return {
        "session_id": session_id,
        "sandbox_id": found["sandbox_id"],
        "state": found.get("state", ""),
        "started_at": found.get("started_at", ""),
        "end_at": found.get("end_at", ""),
        # 本副本的进程内有没有句柄。跟 state 无关，纯粹是排查多副本问题用的：
        # cached=false 而 state=running 就是"这次请求落到了没建它的副本上"。
        "cached_here": get_sandbox_manager().get(session_id) is not None,
    }


@router.delete("/{session_id}")
async def destroy_session(session_id: str):
    """删掉会话的沙箱。

    ## 三种结果，必须分开报

      200 found=true    删掉了
      200 found=false   本来就没有（回收器先删了 / 从没建过）—— DELETE 是幂等的，
                        用户要的"让它不存在"已经成立
      503               Cube 问不到，或者删的时候它拒绝了（paused 沙箱 restore
                        失败返 409、状态转换中返 503）。**不知道有没有删掉**，
                        必须让调用方重试

    只有这里的 KeyError 翻成 200 而不是 404 —— 跟 exec/upload/download 不一样。
    那几个是"要用它"，不存在就是真的做不了事；这个是"要它没了"，不存在恰好就是
    目标状态。前端为一次成功的删除弹一个红色 404 是纯粹的噪音。

    找不到和删掉了仍然分开报（found 字段），因为调用方偶尔需要知道 —— 验收脚本要
    断言"删之前它确实在"，这个字段是唯一的判据。
    """
    mgr = get_sandbox_manager()
    try:
        await mgr.destroy(session_id)
    except SandboxLookupError:
        raise HTTPException(503, _UNAVAILABLE)
    except KeyError:
        return {"status": "ok", "found": False}
    except Exception as e:
        # _kill_backend(propagate=True) 抛上来的。Cube 明确拒绝了这次删除。
        raise HTTPException(503, f"sandbox delete failed, retry: {e}")
    return {"status": "ok", "found": True}
