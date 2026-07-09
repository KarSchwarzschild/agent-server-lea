"""Admin Skill/Tool 管理 API（Ph1 Skill v2 · Anthropic Skills 格式对齐）。

约定：
- Skill DB schema 见 lea_server.models.skill.Skill（无 execution_mode/context/allowed_tools/resources）
- 用户创建的 skill 默认走 ReAct（pipeline_class=''）；仅 registry 中的 builtin 用 pipeline
- 内置 skill 只允许编辑 UI 白名单字段（与 skill_service._UI_EDITABLE_COLS 对齐）
- POST /admin/skills/sync 支持 ?mode=insert_only|upsert（默认 insert_only）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from lea_graph.skills.schema import (
    SKILL_STATUS_PUBLISHED,
    SKILL_STATUS_VALUES,
)
from lea_server.core.deps import admin_required
from lea_server.core.tenant import DEFAULT_TENANT_ID
from lea_server.database import get_db
from lea_server.models.models import User
from lea_server.models.skill import Skill, SkillVersion
from lea_server.models.tool import Tool
from lea_server.services.skill_service import SkillService

router = APIRouter(prefix="/admin", tags=["admin-skills"])

_require_admin = admin_required

# 内置 skill 允许通过 PUT 编辑的字段（对齐 SkillService._UI_EDITABLE_COLS）
_BUILTIN_EDITABLE_FIELDS = {
    "display_name",
    "category",
    "model_tier",
    "required_tools",
    "status",
    "scope",
}


# Tool CRUD API 已挪到 lea_server/api/admin_tools.py（Sprint A · A1）


def _validate_required_tools(db: Session, required_tools: list[str] | None) -> None:
    """校验 required_tools 里的每个 name 都在 tool 表存在。

    契约：Skill.required_tools 是「硬依赖」—— Agent 挂了 skill 就自动继承这些 tool。
    如果这里放行一个不存在的 tool.name，运行时 agent 拿到的就是空指针。

    - `mcp:*` 前缀跳过（MCP tool 由 mcp_servers 动态入表，Sprint E 才激活）
    - 空 list / None 直接放行
    """
    if not required_tools:
        return
    to_check = [n for n in required_tools if not n.startswith("mcp:")]
    if not to_check:
        return
    existing = {row[0] for row in db.query(Tool.name).filter(Tool.name.in_(to_check)).all()}
    missing = sorted(set(to_check) - existing)
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"required_tools 引用了不存在的 tool: {missing}",
        )


# ── Skill CRUD API ──────────────────────────────────────────


class SkillCreateRequest(BaseModel):
    """创建 skill 元数据。skill 内容（system prompt / scripts / references）走 SG-5 bundle 上传 API 挂载。"""
    name: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=200)
    description: str = ""  # 可包含 "Triggers: A, B, C" 子句
    category: str = "general"
    version: str = "1.0.0"
    model_tier: str = "mid"
    required_tools: list[str] = []
    allowed_roles: list[str] = []  # 空=不限，非空=角色白名单
    status: str = SKILL_STATUS_PUBLISHED


class SkillUpdateRequest(BaseModel):
    display_name: str | None = None
    description: str | None = None
    category: str | None = None
    version: str | None = None
    model_tier: str | None = None
    required_tools: list[str] | None = None
    allowed_roles: list[str] | None = None
    status: str | None = None
    scope: str | None = None


def _skill_to_dict(skill: Skill) -> dict:
    return {
        "id": skill.id,
        "tenant_id": skill.tenant_id,
        "name": skill.name,
        "display_name": skill.display_name,
        "description": skill.description or "",
        "version": skill.version or "1.0.0",
        "category": skill.category or "general",
        "model_tier": skill.model_tier or "mid",
        "required_tools": skill.required_tools or [],
        "allowed_roles": skill.allowed_roles or [],
        "pipeline_class": skill.pipeline_class or "",
        "status": skill.status or SKILL_STATUS_PUBLISHED,
        "scope": skill.scope or "private",
        "is_builtin": skill.is_builtin,
        "created_by": skill.created_by,
        # OBS Bundle 元数据（SG-2 起）
        "bundle_uri": skill.bundle_uri or "",
        "bundle_hash": skill.bundle_hash or "",
        "bundle_size": skill.bundle_size or 0,
        "created_at": skill.created_at.isoformat() if skill.created_at else None,
        "updated_at": skill.updated_at.isoformat() if skill.updated_at else None,
    }


def _get_redis(request: Request):
    return getattr(request.app.state, "redis", None)


@router.get("/skills")
async def list_skills(
    request: Request,
    scope: str | None = None,
    category: str | None = None,
    status: str | None = None,
    page: int = 1,
    size: int = 50,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """管理员查看所有 Skill（默认展示全部状态）。"""
    svc = SkillService(db, redis_client=_get_redis(request))
    # 管理员默认看 all（不限 status），除非显式指定
    skills, total = svc.list_skills(
        scope=scope,
        category=category,
        status=status,  # None → all
        page=page,
        size=size,
    )
    return {
        "skills": [_skill_to_dict(s) for s in skills],
        "total": total,
        "page": page,
        "size": size,
    }


@router.post("/skills")
async def create_skill(
    req: SkillCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(_require_admin),
):
    """创建公共 Skill（scope=public，pipeline_class=''，走 ReAct）。"""
    if req.status not in SKILL_STATUS_VALUES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{req.status}', must be one of {SKILL_STATUS_VALUES}",
        )

    svc = SkillService(db, redis_client=_get_redis(request))
    if svc.get_skill_by_name(req.name):
        raise HTTPException(status_code=400, detail=f"Skill '{req.name}' 已存在")

    _validate_required_tools(db, req.required_tools)

    data = req.model_dump()
    data["scope"] = "public"
    data["pipeline_class"] = ""  # 用户创建的 skill 走 ReAct
    try:
        skill = svc.create_skill(data, created_by=admin.user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return _skill_to_dict(skill)


@router.get("/skills/{skill_id}")
async def get_skill(
    skill_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    skill = SkillService(db, redis_client=_get_redis(request)).get_skill(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill 不存在")
    return _skill_to_dict(skill)


@router.put("/skills/{skill_id}")
async def update_skill(
    skill_id: int,
    req: SkillUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    svc = SkillService(db, redis_client=_get_redis(request))
    skill = svc.get_skill(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill 不存在")

    data = req.model_dump(exclude_none=True)

    if data.get("status") and data["status"] not in SKILL_STATUS_VALUES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status, must be one of {SKILL_STATUS_VALUES}",
        )

    # 内置 skill 只允许编辑 UI 白名单字段
    if skill.is_builtin:
        forbidden = set(data.keys()) - _BUILTIN_EDITABLE_FIELDS
        if forbidden:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"内置 Skill 只允许编辑 {sorted(_BUILTIN_EDITABLE_FIELDS)}；"
                    f"禁止编辑 {sorted(forbidden)}"
                ),
            )

    if "required_tools" in data:
        _validate_required_tools(db, data["required_tools"])

    skill = svc.update_skill(skill_id, data)
    db.commit()
    return _skill_to_dict(skill)


@router.delete("/skills/{skill_id}")
async def delete_skill(
    skill_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    svc = SkillService(db, redis_client=_get_redis(request))
    try:
        svc.delete_skill(skill_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return {"message": "已删除"}


@router.post("/skills/sync")
async def sync_skills(
    request: Request,
    mode: str = Query("insert_only", pattern="^(insert_only|upsert)$"),
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """手动触发 skill.md 同步。

    - `mode=insert_only`（默认）：已存在的 skill 完全跳过（连附件都不刷）
    - `mode=upsert`：只覆盖 MD 派生字段（description/version/pipeline_class）；
      bundle_* 走 SG-4 seed；UI 可编辑字段永不覆盖
    """
    from pathlib import Path

    import lea_graph
    from lea_graph.skills.loader import SkillLoader

    builtin_dir = Path(lea_graph.__file__).resolve().parent / "skills" / "builtin"
    if not builtin_dir.is_dir():
        raise HTTPException(status_code=500, detail=f"builtin 目录不存在: {builtin_dir}")

    loader = SkillLoader([str(builtin_dir)])
    definitions = list(loader.load_all().values())
    if not definitions:
        return {"message": "no builtin skills found", "stats": {}}

    svc = SkillService(db, redis_client=_get_redis(request))
    from lea_graph.skills.bundle_store import SkillBundleStore
    try:
        bundle_store = SkillBundleStore.from_env()
    except Exception:
        bundle_store = None
    stats = svc.sync_builtins(
        definitions, mode=mode, tenant_id=DEFAULT_TENANT_ID, bundle_store=bundle_store
    )
    return {"message": f"sync {mode} done", "stats": stats}


# ══════════════════════════════════════════════════════════════
#  SG-5 · Bundle 上传 / 版本管理 / 回滚
# ══════════════════════════════════════════════════════════════


@router.post("/skills/{skill_id}/bundle")
async def upload_skill_bundle(
    skill_id: int,
    request: Request,
    file: UploadFile = File(..., description="skill 打包好的 tar.gz（根下必须只含一个顶层目录，且顶层目录含 skill.md）"),
    change_log: str = "",
    db: Session = Depends(get_db),
    admin: User = Depends(_require_admin),
):
    """上传新的 skill bundle。

    流程：
      1. 收 multipart tar.gz → 内存
      2. 解压到临时目录 → 校验（无 traversal / symlink / skill.md 必须存在）
      3. 用 SkillBundleStore 重新确定性 pack（同内容 → 同 sha256，服务端权威 hash）
      4. push OBS（HEAD 命中则复用旧对象）
      5. 更新 skills.bundle_* + 旧 SkillVersion 降位 + 插入新 SkillVersion is_current=True
      6. 广播 skill_reload 让所有节点 hydrate 新 hash

    如果 bundle_hash 与当前 skill.bundle_hash 一致 → 幂等 no-op，不新增版本记录。
    """
    import io
    import shutil
    import tarfile
    import tempfile
    from pathlib import Path

    from lea_graph.skills.bundle_store import (
        BUNDLE_MAX_BYTES,
        BundleError,
        SkillBundleStore,
        _safe_extract,
    )
    from lea_graph.skills.schema import SkillDefinition

    svc = SkillService(db, redis_client=_get_redis(request))
    skill = svc.get_skill(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill 不存在")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="空文件")
    if len(raw) > BUNDLE_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"bundle 超出上限 {BUNDLE_MAX_BYTES} 字节 (收到 {len(raw)})",
        )

    tmp_root = Path(tempfile.mkdtemp(prefix="lea-skill-upload-"))
    try:
        # 步骤 1：解压 + 严格校验（含 path traversal / symlink 防护）
        try:
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
                _safe_extract(tf, tmp_root)
        except (BundleError, tarfile.TarError) as e:
            raise HTTPException(status_code=400, detail=f"tar 解压失败: {e}") from None

        # 步骤 2：定位顶层目录（bundle 内必须是单顶层目录）
        entries = [p for p in tmp_root.iterdir() if not p.name.startswith(".")]
        if len(entries) != 1 or not entries[0].is_dir():
            raise HTTPException(
                status_code=400,
                detail="bundle 结构错误：根下必须只含一个目录",
            )
        top_dir = entries[0]
        if not (top_dir / "skill.md").exists():
            raise HTTPException(
                status_code=400,
                detail=f"顶层目录 '{top_dir.name}' 缺 skill.md",
            )

        # 步骤 3：服务端确定性重新 pack（拿权威 hash）
        try:
            store = SkillBundleStore.from_env()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"对象存储不可用: {e}") from None
        try:
            bundle = store.pack(top_dir)
        except BundleError as e:
            raise HTTPException(status_code=400, detail=f"bundle 打包失败: {e}") from None

        # 步骤 4：幂等短路 —— 上传的和当前完全一致
        if skill.bundle_hash == bundle.sha256:
            return {
                "message": "bundle 与当前版本一致，无变化",
                "skill_id": skill.id,
                "bundle_hash": bundle.sha256,
                "bundle_size": bundle.size,
                "changed": False,
            }

        # 步骤 5：push OBS
        uri = store.push(bundle)

        # 步骤 6：DB 更新（复用 SkillService 的 bundle 变更逻辑）
        defn_shim = SkillDefinition(
            name=skill.name,
            version=skill.version or "1.0.0",
            bundle_uri=uri,
            bundle_hash=bundle.sha256,
            bundle_size=bundle.size,
            bundle_manifest=dict(bundle.manifest),
        )
        svc._apply_bundle_change(
            skill,
            defn_shim,
            change_log=change_log or f"admin upload by {admin.user_id}",
        )
        db.commit()
        svc.notify_change()

        return {
            "message": "bundle 上传成功",
            "skill_id": skill.id,
            "bundle_uri": uri,
            "bundle_hash": bundle.sha256,
            "bundle_size": bundle.size,
            "manifest_files": len(bundle.manifest),
            "changed": True,
        }
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


@router.get("/skills/{skill_id}/versions")
async def list_skill_versions(
    skill_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """查询 skill 的所有历史版本（按 created_at desc）。"""
    skill = db.query(Skill).filter(Skill.id == skill_id).first()
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill 不存在")

    versions = (
        db.query(SkillVersion)
        .filter(SkillVersion.skill_id == skill_id)
        .order_by(SkillVersion.created_at.desc())
        .all()
    )
    return {
        "skill_id": skill_id,
        "skill_name": skill.name,
        "current_hash": skill.bundle_hash or "",
        "versions": [
            {
                "id": v.id,
                "version_num": v.version_num,
                "bundle_hash": v.bundle_hash,
                "bundle_size": v.bundle_size,
                "bundle_uri": v.bundle_uri,
                "is_current": bool(v.is_current),
                "change_log": v.change_log or "",
                "created_by": v.created_by,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in versions
        ],
    }


class RollbackRequest(BaseModel):
    version_id: int = Field(description="要回滚到的 skill_versions.id")


@router.post("/skills/{skill_id}/rollback")
async def rollback_skill_bundle(
    skill_id: int,
    req: RollbackRequest,
    request: Request,
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """回滚 skill 到某个历史版本。

    把 target version.is_current=True，其他 version.is_current=False，
    skills.bundle_* 覆盖为 target 的 bundle_*。触发 skill_reload。
    """
    svc = SkillService(db, redis_client=_get_redis(request))
    skill = svc.get_skill(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill 不存在")

    target = (
        db.query(SkillVersion)
        .filter(SkillVersion.id == req.version_id, SkillVersion.skill_id == skill_id)
        .first()
    )
    if target is None:
        raise HTTPException(status_code=404, detail=f"version_id={req.version_id} 不属于该 skill")

    if target.is_current and skill.bundle_hash == target.bundle_hash:
        return {
            "message": "已是当前版本，无变化",
            "changed": False,
            "current_version_id": target.id,
            "current_hash": target.bundle_hash,
        }

    # 全部降位 + target 升位
    db.query(SkillVersion).filter(
        SkillVersion.skill_id == skill_id,
        SkillVersion.is_current.is_(True),
    ).update({"is_current": False})
    target.is_current = True

    # 主表覆盖 bundle_*
    skill.bundle_uri = target.bundle_uri
    skill.bundle_hash = target.bundle_hash
    skill.bundle_size = target.bundle_size
    skill.bundle_manifest = dict(target.bundle_manifest or {})

    db.commit()
    svc.notify_change()

    return {
        "message": "已回滚",
        "changed": True,
        "current_version_id": target.id,
        "current_hash": target.bundle_hash,
        "version_num": target.version_num,
    }


# ══════════════════════════════════════════════════════════════
#  Skill 在线测试（需求 6）
# ══════════════════════════════════════════════════════════════


class SkillTestRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


@router.post("/skills/{skill_id}/test")
async def test_skill(
    skill_id: int,
    req: SkillTestRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(_require_admin),
):
    """在线测试 Skill —— 发送一条消息，SSE 流式返回执行结果。

    不创建 session、不持久化消息，纯粹用于开发调试。
    复用 execute_skill_as_sse 完整管线（含 bundle hydrate + sandbox）。
    执行完成后写一条 skill_invocations 记录供统计。
    """
    import time

    from fastapi.responses import StreamingResponse

    from lea_graph.skills.hydrator import hydrate_bundle
    from lea_graph.skills.runner import execute_skill_as_sse

    svc = SkillService(db, redis_client=_get_redis(request))
    skill = svc.get_skill(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill 不存在")

    definition = hydrate_bundle(svc.to_definition(skill))

    registry = request.app.state.registry
    all_tools = _collect_all_tools(registry)

    thread_id = f"test:{admin.user_id}:{skill_id}"

    async def _gen_with_record():
        start = time.time()
        status = "success"
        error_msg = None
        try:
            async for chunk in execute_skill_as_sse(
                definition, req.message, all_tools, thread_id=thread_id
            ):
                yield chunk
        except Exception as e:
            status = "error"
            error_msg = str(e)[:500]
            raise
        finally:
            duration = round((time.time() - start) * 1000)
            _record_invocation(
                skill_id=skill_id,
                skill_name=skill.name,
                user_id=admin.user_id,
                session_id=None,
                source="test",
                status=status,
                duration_ms=duration,
                error_message=error_msg,
            )

    return StreamingResponse(
        _gen_with_record(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _collect_all_tools(registry) -> dict:
    """收集所有可用 Tool（与 chat.py 同逻辑，避免跨模块 import 循环）。"""
    from langchain_core.tools import BaseTool
    from lea_graph.mcp.client import get_mcp_client
    from lea_graph.tools import get_platform_tools

    tools: dict[str, BaseTool] = {}
    for t in get_platform_tools():
        tools[t.name] = t
    for name, app_instance in registry.apps.items():
        for t in app_instance.skills:
            tools[t.name] = t
    mcp = get_mcp_client()
    if mcp:
        for t in mcp.get_tools():
            tools[t.name] = t
    return tools


def _record_invocation(
    *,
    skill_id: int | None,
    skill_name: str,
    user_id: str | None,
    session_id: str | None,
    source: str,
    status: str = "success",
    input_tokens: int = 0,
    output_tokens: int = 0,
    duration_ms: int = 0,
    error_message: str | None = None,
) -> None:
    """写一条 skill_invocations 记录（fire-and-forget，失败不阻断主流程）。"""
    from lea_server.database import SessionLocal
    from lea_server.models.skill import SkillInvocation

    try:
        db = SessionLocal()
        db.add(SkillInvocation(
            skill_id=skill_id,
            skill_name=skill_name,
            user_id=user_id,
            session_id=session_id,
            source=source,
            status=status,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=duration_ms,
            error_message=error_message,
        ))
        db.commit()
    except Exception:
        pass
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════
#  Skill 使用统计（需求 12）
# ══════════════════════════════════════════════════════════════


@router.get("/skills/stats")
async def skill_stats(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """Skill 使用统计汇总 —— 按 skill 聚合最近 N 天的调用数据。

    返回每个 skill 的：total_invocations, success_count, error_count,
    avg_duration_ms, total_input_tokens, total_output_tokens。
    """
    from datetime import datetime, timedelta

    from sqlalchemy import case, func as sa_func

    from lea_server.models.skill import SkillInvocation

    since = datetime.utcnow() - timedelta(days=days)

    rows = (
        db.query(
            SkillInvocation.skill_id,
            SkillInvocation.skill_name,
            sa_func.count().label("total"),
            sa_func.sum(case(
                (SkillInvocation.status == "success", 1), else_=0
            )).label("success"),
            sa_func.sum(case(
                (SkillInvocation.status == "error", 1), else_=0
            )).label("errors"),
            sa_func.avg(SkillInvocation.duration_ms).label("avg_duration_ms"),
            sa_func.sum(SkillInvocation.input_tokens).label("input_tokens"),
            sa_func.sum(SkillInvocation.output_tokens).label("output_tokens"),
        )
        .filter(SkillInvocation.created_at >= since)
        .group_by(SkillInvocation.skill_id, SkillInvocation.skill_name)
        .order_by(sa_func.count().desc())
        .all()
    )

    return {
        "days": days,
        "skills": [
            {
                "skill_id": r.skill_id,
                "skill_name": r.skill_name,
                "total_invocations": r.total,
                "success_count": r.success or 0,
                "error_count": r.errors or 0,
                "avg_duration_ms": round(r.avg_duration_ms or 0),
                "total_input_tokens": r.input_tokens or 0,
                "total_output_tokens": r.output_tokens or 0,
            }
            for r in rows
        ],
    }


@router.get("/skills/{skill_id}/stats")
async def skill_detail_stats(
    skill_id: int,
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """单个 Skill 的详细统计 —— 含按天/按来源分组。"""
    from datetime import datetime, timedelta

    from sqlalchemy import case, func as sa_func

    from lea_server.models.skill import SkillInvocation

    skill = db.query(Skill).filter(Skill.id == skill_id).first()
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill 不存在")

    since = datetime.utcnow() - timedelta(days=days)
    base = db.query(SkillInvocation).filter(
        SkillInvocation.skill_id == skill_id,
        SkillInvocation.created_at >= since,
    )

    total = base.count()
    success = base.filter(SkillInvocation.status == "success").count()
    errors = base.filter(SkillInvocation.status == "error").count()

    avg_dur = db.query(sa_func.avg(SkillInvocation.duration_ms)).filter(
        SkillInvocation.skill_id == skill_id,
        SkillInvocation.created_at >= since,
    ).scalar() or 0

    by_source = (
        db.query(
            SkillInvocation.source,
            sa_func.count().label("count"),
        )
        .filter(
            SkillInvocation.skill_id == skill_id,
            SkillInvocation.created_at >= since,
        )
        .group_by(SkillInvocation.source)
        .all()
    )

    by_day = (
        db.query(
            sa_func.date(SkillInvocation.created_at).label("day"),
            sa_func.count().label("count"),
        )
        .filter(
            SkillInvocation.skill_id == skill_id,
            SkillInvocation.created_at >= since,
        )
        .group_by(sa_func.date(SkillInvocation.created_at))
        .order_by(sa_func.date(SkillInvocation.created_at))
        .all()
    )

    return {
        "skill_id": skill_id,
        "skill_name": skill.name,
        "days": days,
        "total_invocations": total,
        "success_count": success,
        "error_count": errors,
        "avg_duration_ms": round(avg_dur),
        "by_source": {r.source: r.count for r in by_source},
        "by_day": [
            {"date": str(r.day), "count": r.count} for r in by_day
        ],
    }
