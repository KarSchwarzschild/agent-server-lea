"""Skill 路由 —— 三层可见性 + 上架审核 + bundle 上传/下载。

## 三层可见性

归属由 `(tenant_id, owner_scope, owner_user_id)` 三元组表达，跟 tools 表同一套编码：

    用户可见    (t1,      user,     alice)   只有 alice
    租户下可见  (t1,      platform, NULL)    t1 所有人
    所有人可见  (default, platform, NULL)    所有租户

上传默认落**用户可见**。往上走要走 `POST /{id}/publish` 提申请，由对应层级的管理员
审核（见 models/skill_publish.py）。

## 为什么不用 scoped_filter

`lea_authkit.scoped_filter` 最后一句是 `and_(tenant_id == actor.tenant_id, ...)` ——
它**表达不了「所有人可见」那一层**，因为那些行住在 `tenant_id='default'`。用它的话
平台市场里的 skill 对任何租户用户都不可见。

所以这里用本地的 `_visible_skills`，跟 lea-tools 的 `visible_tools` 是同一个形状
（`tenant_id.in_([自己的, 全局])`）。写死在这个文件里而不是改 authkit：那个函数被
tools / kb / mcp 共用，改它的语义要一起验，不该顺手做。

## 旧编码

`scope`（private/system/public）和 `status`（draft/active/published）两列由
migrations/014 删除。status 一列兼三个意思（上架/启用/生命周期），而 upload 写的
'active' 在三个语义里哪个都对不上 —— 实测后果是 6 个真 skill 全部 enabled=false、
普通用户列表里一个都看不见。拆开之后：可见性归三元组，启用归 `enabled`。
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import zipfile
from functools import lru_cache

import yaml
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from lea_authkit import Actor, require_actor
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lea_config.database import (
    DEFAULT_TENANT_ID,
    OWNER_SCOPE_PLATFORM,
    OWNER_SCOPE_USER,
    get_db,
)
from lea_config.models.binding import AgentSkillBinding
from lea_config.models.publication import (
    PUBLICATION_APPROVED as PUBLISH_APPROVED,
)
from lea_config.models.publication import (
    PUBLICATION_PENDING as PUBLISH_PENDING,
)
from lea_config.models.publication import (
    PUBLICATION_REJECTED as PUBLISH_REJECTED,
)
from lea_config.models.publication import (
    PUBLICATION_TARGET_TENANT as PUBLISH_TARGET_TENANT,
)
from lea_config.models.publication import (
    PUBLICATION_TARGETS as PUBLISH_TARGETS,
)
from lea_config.models.publication import (
    PublicationRequest,
)
from lea_config.models.skill import Skill, SkillVersion
from lea_config.models.tool import Tool
from lea_config.services.publication import (
    RESOURCE_SKILL,
    cancel_pending_for_resource,
    cancel_request,
    publication_request_dict,
    record_audit_event,
    review_request,
    scoped_requests,
    submit_skill_request,
)

router = APIRouter(prefix="/skills", tags=["skills"])

# 可见性层级的对外名字。跟库里的三元组一一对应，但对客户端只暴露这一个词 ——
# 让前端拼三元组等于把编码泄漏出去，以后想改存储就动不了。
VIS_PRIVATE = "private"  # (t1, user, alice)
VIS_TENANT = "tenant"  # (t1, platform, NULL)
VIS_GLOBAL = "global"  # (default, platform, NULL)


# ── Schemas ────────────────────────────────────────────────

class SkillCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    display_name: str = ""
    description: str = ""
    version: str = "1.0.0"
    category: str = Field(min_length=1, max_length=50)
    system_prompt: str = ""
    model_tier: str = "standard"
    required_tools: list = []
    bundle_uri: str | None = None
    # 前端兼容字段（收下但不落库）
    allowed_tools: list[str] = []
    trigger_keywords: list[str] = []
    resources: dict = {}

    # 归属**不接受调用方指定** —— 建出来一律是创建者私有，要往上走走审核。
    # 让 body 能设归属等于自助提权成「全租户可用」，这跟 tools 那边是同一条规则
    # （见 api/tools.py 的 create_tool）。


class SkillUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    category: str | None = None
    system_prompt: str | None = None
    model_tier: str | None = None
    required_tools: list | None = None
    enabled: bool | None = None
    # name 不给改：agent_skill_bindings 按 id 绑，但投递和披露都按 name
    # （sessions/<sid>/skills/<name>/ 和引擎的 skill.Skill.Name）。改名会让已投递的
    # 目录跟新名字对不上，而那个错要到模型去读 SKILL.md 时才显形。
    allowed_tools: list[str] | None = None
    trigger_keywords: list[str] | None = None
    resources: dict | None = None


class PublishRequestBody(BaseModel):
    target_scope: str = Field(description="tenant = 租户内可见；global = 所有租户可见")
    note: str = ""


class ReviewBody(BaseModel):
    approve: bool
    note: str = ""


class SkillPublicationCancelBody(BaseModel):
    note: str = ""


# ── Helpers ────────────────────────────────────────────────

def _is_admin(actor: Actor) -> bool:
    return actor.is_platform_admin or actor.is_tenant_admin


def _visibility_of(skill: Skill) -> str:
    if skill.owner_scope == OWNER_SCOPE_USER:
        return VIS_PRIVATE
    return VIS_GLOBAL if skill.tenant_id == DEFAULT_TENANT_ID else VIS_TENANT


def _visible_skills(db: Session, actor: Actor):
    """actor 能看见的 skill。

    三层各自的条件：

        所有人可见  tenant_id = 'default' 且 owner_scope = 'platform'
        租户下可见  tenant_id = 自己的     且 owner_scope = 'platform'
        用户可见    tenant_id = 自己的     且 owner_scope = 'user' 且 owner_user_id = 自己

    平台管理员无约束（跟 scoped_filter 的语义一致）。租户管理员看得到本租户全部，
    包括别人的私有 skill —— 他要审核就得先看得见。
    """
    q = db.query(Skill)
    if actor.is_platform_admin:
        return q

    tenant_clause = Skill.tenant_id.in_([actor.tenant_id, DEFAULT_TENANT_ID])

    if actor.is_tenant_admin:
        # 本租户全部 + 全局市场
        return q.filter(tenant_clause)

    return q.filter(
        tenant_clause,
        or_(
            Skill.owner_scope == OWNER_SCOPE_PLATFORM,
            and_(
                Skill.owner_scope == OWNER_SCOPE_USER,
                Skill.owner_user_id == actor.user_id,
            ),
        ),
    )


def _load_visible(db: Session, actor: Actor, skill_id: int) -> Skill:
    """按 id 取一个 actor 看得见的 skill，看不见一律 404。

    404 而不是 403：403 会确认「这个 id 存在」，而跨租户不该给出这个信息。
    """
    skill = _visible_skills(db, actor).filter(Skill.id == skill_id).first()
    if skill is None:
        raise HTTPException(404, "Skill 不存在")
    return skill


def _can_edit(skill: Skill, actor: Actor) -> bool:
    if actor.is_platform_admin:
        return True
    if skill.tenant_id != actor.tenant_id:
        return False  # 全局市场的东西，租户管理员也改不了
    if actor.is_tenant_admin:
        return True
    return (
        skill.owner_scope == OWNER_SCOPE_USER
        and skill.owner_user_id == actor.user_id
    )


def _skill_to_dict(skill: Skill, actor: Actor, pending: str | None = None) -> dict:
    can_edit = _can_edit(skill, actor)
    publication_status = (
        "pending"
        if pending
        else (
            "draft"
            if skill.owner_scope == OWNER_SCOPE_USER
            else ("active" if skill.enabled else "suspended")
        )
    )
    allowed_actions = ["read"]
    if can_edit:
        allowed_actions.extend(["update", "delete"])
    if skill.owner_scope == OWNER_SCOPE_USER and can_edit and not pending:
        allowed_actions.append("submit_publication")
    return {
        "id": skill.id,
        "name": skill.name,
        "display_name": skill.display_name or skill.name,
        "description": skill.description or "",
        "category": skill.category or "general",
        "version": skill.version or "1.0.0",
        "model_tier": skill.model_tier or "standard",
        "allowed_tools": skill.required_tools or [],
        "visibility": _visibility_of(skill),
        "enabled": bool(skill.enabled),
        "is_builtin": bool(skill.is_builtin),
        "has_bundle": bool(skill.bundle_uri),
        "is_own": bool(actor.user_id and skill.created_by == actor.user_id),
        "can_edit": can_edit,
        # 有在审的申请时带出目标层，前端据此把「申请上架」按钮变成「审核中」。
        # None = 没有在审的。
        "pending_publish": pending,
        "system_prompt": skill.system_prompt or "",
        "publication_status": publication_status,
        "allowed_actions": allowed_actions,
    }


def _version_snapshot(skill: Skill, version: str, bundle_uri: str | None = None) -> dict:
    return {
        "name": skill.name,
        "display_name": skill.display_name or skill.name,
        "description": skill.description or "",
        "category": skill.category or "general",
        "version": version,
        "model_tier": skill.model_tier or "standard",
        "required_tools": skill.required_tools or [],
        "system_prompt": skill.system_prompt or "",
        "enabled": bool(skill.enabled),
        "bundle_uri": bundle_uri if bundle_uri is not None else skill.bundle_uri,
    }


def _version_to_dict(row: SkillVersion) -> dict:
    return {
        "id": row.id,
        "skill_id": row.skill_id,
        "version": row.version_num,
        "snapshot": row.snapshot or {},
        "change_log": row.change_log or "",
        "bundle_uri": row.bundle_uri,
        "sha256": row.sha256,
        "size_bytes": row.size_bytes or 0,
        "created_by": row.created_by,
        "created_at": row.created_at,
    }


def _skill_version_view(skill: Skill, actor: Actor, version: SkillVersion,
                        pending: str | None = None) -> dict:
    data = _skill_to_dict(skill, actor, pending)
    snapshot = version.snapshot or {}
    data.update({
        "version_id": version.id,
        "version": version.version_num,
        "description": snapshot.get("description", data["description"]),
        "display_name": snapshot.get("display_name", data["display_name"]),
        "category": snapshot.get("category", data["category"]),
        "model_tier": snapshot.get("model_tier", data["model_tier"]),
        "allowed_tools": snapshot.get("required_tools", data["allowed_tools"]),
        "system_prompt": snapshot.get("system_prompt", data["system_prompt"]),
        "has_bundle": bool(version.bundle_uri),
        "change_log": version.change_log or "",
        "size_bytes": version.size_bytes or 0,
        "sha256": version.sha256,
        "created_at": version.created_at,
    })
    return data


def _legacy_publish_request_dict(request: PublicationRequest) -> dict:
    data = publication_request_dict(request)
    return {
        "id": data["id"],
        "skill_id": int(data["resource_id"]),
        "tenant_id": data["tenant_id"],
        "requested_by": data["requested_by"],
        "target_scope": data["target_scope"],
        "status": data["status"],
        "review_note": data["review_note"] or data["request_note"],
        "reviewed_by": data["reviewed_by"],
        "reviewed_at": data["reviewed_at"],
        "created_at": data["created_at"],
        "updated_at": data["updated_at"],
    }


def _pending_map(db: Session, skill_ids: list[int]) -> dict[int, str]:
    """一次查出这批 skill 各自有没有在审的申请，避免 N+1。"""
    if not skill_ids:
        return {}
    rows = (
        db.query(PublicationRequest.resource_id, PublicationRequest.target_scope)
        .filter(
            PublicationRequest.resource_type == RESOURCE_SKILL,
            PublicationRequest.resource_id.in_([str(skill_id) for skill_id in skill_ids]),
            PublicationRequest.status == PUBLISH_PENDING,
        )
        .all()
    )
    return {int(resource_id): target for resource_id, target in rows}


# ── SKILL.md Parser ───────────────────────────────────────

def _parse_skill_md(content: str) -> dict:
    if not content.startswith("---"):
        raise ValueError("SKILL.md 缺少 YAML frontmatter（需以 --- 开头）")
    match = re.match(r"^---\s*\r?\n(.*?)\r?\n---\s*(.*)", content, re.DOTALL)
    if not match:
        raise ValueError("SKILL.md frontmatter 格式无效")
    frontmatter = yaml.safe_load(match.group(1))
    if not isinstance(frontmatter, dict):
        raise ValueError("frontmatter 必须是 YAML dict")
    name = frontmatter.get("name", "").strip()
    if not name:
        raise ValueError("frontmatter 缺少 name 字段")
    if not re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$", name) and len(name) > 1:
        if not re.match(r"^[a-z0-9-]+$", name):
            raise ValueError(f"name '{name}' 必须是 kebab-case")
    description = frontmatter.get("description", "").strip()
    version = str(frontmatter.get("version", "1.0.0")).strip()
    if not re.match(r"^v?\d+(?:\.\d+){0,2}(?:[-+][0-9A-Za-z.-]+)?$", version):
        raise ValueError("version 必须是语义化版本号，例如 1.2.0")
    change_log = str(
        frontmatter.get("change-log", frontmatter.get("release-notes", ""))
    ).strip()
    body = match.group(2).strip()
    allowed_tools = frontmatter.get("allowed-tools", [])
    if isinstance(allowed_tools, str):
        allowed_tools = [t.strip() for t in allowed_tools.split(",")]
    return {
        "name": name,
        "description": description,
        "version": version.removeprefix("v"),
        "change_log": change_log,
        "system_prompt": body,
        "required_tools": allowed_tools or [],
    }


def _find_skill_md(zf: zipfile.ZipFile) -> str | None:
    for name in zf.namelist():
        basename = name.rsplit("/", 1)[-1] if "/" in name else name
        if basename == "SKILL.md":
            parts = name.split("/")
            if len(parts) <= 2:
                return name
    return None


# ── 固定路径优先 ──────────────────────────────────────────
#
# ⚠️ 这一段必须排在 /{skill_id} 之前。FastAPI 按声明顺序匹配，放在后面的话
# GET /skills/publish-requests 会被 /{skill_id} 捕获，然后 int 转换失败返 422。

@router.get("/tools")
def list_available_tools(
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    """创建 Skill 时可选的 Tool 列表。"""
    rows = (
        db.query(Tool.name, Tool.description)
        .filter(
            Tool.enabled.is_(True),
            Tool.tenant_id.in_([actor.tenant_id, DEFAULT_TENANT_ID]),
        )
        .order_by(Tool.category, Tool.name)
        .all()
    )
    return {"tools": [{"name": n, "description": d or ""} for n, d in rows]}


@router.get("/publish-requests")
def list_publish_requests(
    status: str = Query(PUBLISH_PENDING),
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    """待审列表。按角色分：租户管理员看 tenant 档，平台管理员看 global 档。

    普通用户看自己提的（用来查「我的申请被驳回了没、理由是什么」）。
    """
    rows = (
        scoped_requests(db, actor)
        .filter(
            PublicationRequest.resource_type == RESOURCE_SKILL,
            PublicationRequest.status == status,
        )
        .order_by(PublicationRequest.created_at.desc(), PublicationRequest.id.desc())
        .limit(200)
        .all()
    )

    # 带上 skill 名字，否则待审列表只有一串 id
    names = dict(
        db.query(Skill.id, Skill.name)
        .filter(Skill.id.in_([int(r.resource_id) for r in rows]))
        .all()
    ) if rows else {}

    return {
        "requests": [
            {
                **_legacy_publish_request_dict(r),
                "skill_name": names.get(
                    int(r.resource_id),
                    (r.resource_snapshot or {}).get("name", "(已删除)"),
                ),
            }
            for r in rows
        ]
    }


@router.post("/publish-requests/{request_id}/review")
def review_publish_request(
    request_id: int,
    body: ReviewBody,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    """通过或驳回一份上架申请。

    通过 = 改 skill 的归属列。两档各自的落点：

        tenant  →  (原 tenant_id, platform, NULL)
        global  →  ('default',    platform, NULL)

    ⚠️ global 那一档会把 skill 搬出原租户（tenant_id 改成 'default'）。这是有意的 ——
    「所有租户可见」在这套编码里就是住进全局租户。搬走之后原租户管理员也改不了它了
    （_can_edit 里 tenant_id 不匹配直接 false），只有平台管理员能。
    """
    req = review_request(
        db,
        actor,
        request_id,
        PUBLISH_APPROVED if body.approve else PUBLISH_REJECTED,
        body.note,
    )
    skill = db.query(Skill).filter(Skill.id == int(req.resource_id)).first()

    if not body.approve:
        return {"status": PUBLISH_REJECTED, "skill_id": int(req.resource_id)}

    return {
        "status": PUBLISH_APPROVED,
        "skill_id": skill.id,
        "visibility": _visibility_of(skill),
    }


# ── Skill CRUD ─────────────────────────────────────────────

@router.get("")
def list_skills(
    category: str | None = None,
    visibility: str | None = Query(None, description="private / tenant / global"),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    version: str | None = Query(None, description="只返回拥有该版本的 Skill"),
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    """列表 = 市场。

    默认返回 actor 看得见的全部三层。`visibility=global` 就是「市场页」，
    `visibility=private` 是「我的 skill」。
    """
    q = _visible_skills(db, actor)

    if category:
        q = q.filter(Skill.category == category)

    if visibility == VIS_PRIVATE:
        q = q.filter(
            Skill.owner_scope == OWNER_SCOPE_USER,
            Skill.owner_user_id == actor.user_id,
        )
    elif visibility == VIS_TENANT:
        q = q.filter(
            Skill.owner_scope == OWNER_SCOPE_PLATFORM,
            Skill.tenant_id == actor.tenant_id,
        )
    elif visibility == VIS_GLOBAL:
        q = q.filter(Skill.tenant_id == DEFAULT_TENANT_ID)
    if version:
        q = q.join(SkillVersion, SkillVersion.skill_id == Skill.id).filter(
            SkillVersion.version_num == version
        )

    total = q.count()
    rows = q.order_by(Skill.id).offset((page - 1) * size).limit(size).all()
    pending = _pending_map(db, [s.id for s in rows])

    versions = {}
    if version and rows:
        versions = {
            row.skill_id: row
            for row in db.query(SkillVersion).filter(
                SkillVersion.skill_id.in_([s.id for s in rows]),
                SkillVersion.version_num == version,
            ).all()
        }

    return {
        "skills": [
            _skill_version_view(s, actor, versions[s.id], pending.get(s.id))
            if version and s.id in versions
            else _skill_to_dict(s, actor, pending.get(s.id))
            for s in rows
        ],
        "total": total,
        "page": page,
        "size": size,
    }


@router.get("/{skill_id}")
def get_skill(
    skill_id: int,
    version: str | None = Query(None, description="版本号；不填返回当前版本"),
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    skill = _load_visible(db, actor, skill_id)
    if version:
        row = db.query(SkillVersion).filter(
            SkillVersion.skill_id == skill.id,
            SkillVersion.version_num == version,
        ).first()
        if row is None:
            raise HTTPException(404, "Skill 版本不存在")
        return _skill_version_view(
            skill, actor, row, _pending_map(db, [skill.id]).get(skill.id)
        )
    return _skill_to_dict(skill, actor, _pending_map(db, [skill.id]).get(skill.id))


@router.get("/{skill_id}/versions")
def list_skill_versions(
    skill_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    skill = _load_visible(db, actor, skill_id)
    rows = db.query(SkillVersion).filter(
        SkillVersion.skill_id == skill.id
    ).order_by(SkillVersion.created_at.desc(), SkillVersion.id.desc()).all()
    return {"versions": [_version_to_dict(row) for row in rows]}


@router.post("", status_code=201)
def create_skill(
    body: SkillCreate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    """建 skill。**一律落创建者私有**，往上走走审核。

    归属不从 body 读 —— SkillCreate 里根本没有那些字段，而且这里也是从 actor 算的。
    两道防线：schema 收不到，代码不看。任一道单独就够，但两道都在的时候「以后有人给
    schema 加了字段」才不会立刻变成提权。
    """
    data = body.model_dump()

    if body.allowed_tools and not body.required_tools:
        data["required_tools"] = body.allowed_tools
    for k in ("allowed_tools", "trigger_keywords", "resources"):
        data.pop(k, None)

    data["tenant_id"] = actor.tenant_id
    data["created_by"] = actor.user_id
    data["owner_scope"] = OWNER_SCOPE_USER
    data["owner_user_id"] = actor.user_id
    data["is_builtin"] = False
    data["enabled"] = True

    if not data.get("display_name"):
        data["display_name"] = data["name"].replace("-", " ").title()

    skill = Skill(**data)
    db.add(skill)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, f"Skill 名称 {body.name!r} 已存在")
    db.refresh(skill)
    db.add(SkillVersion(
        skill_id=skill.id,
        version_num=skill.version or "1.0.0",
        snapshot=_version_snapshot(skill, skill.version or "1.0.0"),
        created_by=actor.user_id,
    ))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, f"Skill 版本 {skill.version!r} 已存在")
    return _skill_to_dict(skill, actor)


@router.put("/{skill_id}")
def update_skill(
    skill_id: int,
    body: SkillUpdate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    skill = _load_visible(db, actor, skill_id)
    if not _can_edit(skill, actor):
        raise HTTPException(403, "只能编辑自己创建的 Skill")
    if skill.is_builtin and not actor.is_platform_admin:
        raise HTTPException(403, "内置 Skill 不可修改")

    binding_count = (
        db.query(AgentSkillBinding)
        .filter(AgentSkillBinding.skill_id == skill.id, AgentSkillBinding.enabled.is_(True))
        .count()
    )
    before = {
        "enabled": bool(skill.enabled),
        "version": skill.version or "1.0.0",
    }
    data = body.model_dump(exclude_none=True)
    if "allowed_tools" in data:
        data["required_tools"] = data.pop("allowed_tools")
    for k in ("trigger_keywords", "resources"):
        data.pop(k, None)

    for k, v in data.items():
        setattr(skill, k, v)
    pending_invalidated = False
    if data:
        pending_invalidated = bool(
            cancel_pending_for_resource(
                db,
                actor,
                RESOURCE_SKILL,
                str(skill.id),
                "resource changed; resubmit publication request",
            )
        )
    record_audit_event(
        db,
        actor,
        resource_type=RESOURCE_SKILL,
        resource_id=str(skill.id),
        tenant_id=skill.tenant_id,
        action="resource.updated",
        result="success",
        before=before,
        after={"enabled": bool(skill.enabled), "version": skill.version or "1.0.0"},
        reason="publication request invalidated" if pending_invalidated else None,
    )
    db.commit()
    db.refresh(skill)
    result = _skill_to_dict(skill, actor)
    result["impact"] = {
        "active_agent_bindings": binding_count,
        "enabled_changed": before["enabled"] != bool(skill.enabled),
        "publication_request_invalidated": pending_invalidated,
    }
    return result


@router.delete("/{skill_id}")
def delete_skill(
    skill_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    skill = _load_visible(db, actor, skill_id)
    if not _can_edit(skill, actor):
        raise HTTPException(403, "只能删除自己创建的 Skill")
    if skill.is_builtin and not actor.is_platform_admin:
        raise HTTPException(403, "内置 Skill 不可删除")

    binding_count = (
        db.query(AgentSkillBinding)
        .filter(AgentSkillBinding.skill_id == skill.id, AgentSkillBinding.enabled.is_(True))
        .count()
    )
    pending_count = cancel_pending_for_resource(
        db, actor, RESOURCE_SKILL, str(skill.id), "resource deleted"
    )
    record_audit_event(
        db,
        actor,
        resource_type=RESOURCE_SKILL,
        resource_id=str(skill.id),
        tenant_id=skill.tenant_id,
        action="resource.deleted",
        result="success",
        before={"enabled": bool(skill.enabled), "active_agent_bindings": binding_count},
        after={"deleted": True},
        reason="resource deleted",
    )
    # 通用申请没有资源外键；删除时结束 pending，但保留脱敏快照和审核历史。
    db.delete(skill)
    db.commit()
    return {
        "message": "已删除",
        "impact": {
            "active_agent_bindings": binding_count,
            "pending_publication_requests": pending_count,
        },
    }


# ── 上架申请 ───────────────────────────────────────────────

def _submit_publish_request(
    skill_id: int,
    body: PublishRequestBody,
    db: Session,
    actor: Actor,
) -> PublicationRequest:
    """申请把自己的 skill 上架到更高一层。

    只有**创建者本人**能提（管理员想直接搬用 review 那条路，不用先假装申请一次）。
    """
    if body.target_scope not in PUBLISH_TARGETS:
        raise HTTPException(
            422, f"target_scope 只能是 {PUBLISH_TARGETS}，收到 {body.target_scope!r}"
        )

    skill = _load_visible(db, actor, skill_id)

    if skill.owner_user_id != actor.user_id and not _is_admin(actor):
        raise HTTPException(403, "只能申请上架自己创建的 Skill")

    current = _visibility_of(skill)
    if current == VIS_GLOBAL:
        raise HTTPException(409, "已经是所有人可见，没有更高的层了")
    if current == VIS_TENANT and body.target_scope == PUBLISH_TARGET_TENANT:
        raise HTTPException(409, "已经是租户内可见")

    return submit_skill_request(db, actor, skill, body.target_scope, body.note)


@router.post("/{skill_id}/publish", status_code=201)
def request_publish(
    skill_id: int,
    body: PublishRequestBody,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    """兼容旧 Skill 上架接口。"""
    return _legacy_publish_request_dict(
        _submit_publish_request(skill_id, body, db, actor)
    )


@router.post("/{skill_id}/publication-requests", status_code=201)
def create_skill_publication_request(
    skill_id: int,
    body: PublishRequestBody,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    return publication_request_dict(
        _submit_publish_request(skill_id, body, db, actor)
    )


@router.post("/{skill_id}/publication-requests/{request_id}/cancel")
def cancel_skill_publication_request(
    skill_id: int,
    request_id: int,
    body: SkillPublicationCancelBody,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    """用户侧撤回自己的申请；管理员后台使用通用管理路由。"""
    request = db.query(PublicationRequest).filter(
        PublicationRequest.id == request_id,
        PublicationRequest.resource_type == RESOURCE_SKILL,
        PublicationRequest.resource_id == str(skill_id),
    ).first()
    if request is None:
        raise HTTPException(404, "申请不存在")
    return publication_request_dict(cancel_request(db, actor, request_id, body.note))


# ── Bundle 上传 / 下载 ─────────────────────────────────────

@lru_cache(maxsize=1)
def _get_obs_client():
    from obs import ObsClient

    endpoint = os.getenv("OBS_ENDPOINT", "http://localhost:9000")
    region = os.getenv("OBS_REGION", "cn-north-4")
    server = endpoint.removeprefix("https://").removeprefix("http://").rstrip("/")
    client = ObsClient(
        access_key_id=os.getenv("OBS_ACCESS_KEY", ""),
        secret_access_key=os.getenv("OBS_SECRET_KEY", ""),
        server=server,
        region=region,
        is_secure=endpoint.startswith("https://"),
        path_style=os.getenv("OBS_ADDRESSING_STYLE", "virtual") == "path",
    )
    bucket = os.getenv("OBS_BUCKET", "lea-uploads")
    response = client.headBucket(bucket)
    if response.status >= 300:
        client.close()
        raise RuntimeError(f"OBS bucket check failed: {response.status}")
    return client, bucket


def _get_minio_client():
    import boto3
    from botocore.config import Config

    endpoint = os.getenv("MINIO_ENDPOINT", "http://localhost:9000")
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
        aws_secret_access_key=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )
    bucket = os.getenv("MINIO_BUCKET", "lea-uploads")
    try:
        client.head_bucket(Bucket=bucket)
    except client.exceptions.ClientError:
        client.create_bucket(Bucket=bucket)
    return client, bucket


def _storage_backend() -> str:
    configured = os.getenv("OBJECT_STORAGE", "").strip().lower()
    if configured in {"obs", "minio"}:
        return configured
    return "obs" if os.getenv("OBS_ACCESS_KEY") else "minio"


@router.post("/upload", status_code=201)
def upload_skill_bundle(
    file: UploadFile = File(...),
    version: str = Form(..., min_length=1, max_length=50),
    category: str = Form(..., min_length=1, max_length=50),
    change_log: str = Form("", max_length=5000),
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    """上传 skill 包。**普通用户也能传**，建出来是自己私有的。

    以前这里第一行是 `require_admin(actor)` —— 那把「用户能上传」整个挡死了。
    归属改成从 actor 算：一律落用户私有，要给别人用走 /publish 审核。

    覆盖语义：同一个租户下同名的 skill 会被更新（bundle 换新、正文重解析），
    但**归属不动** —— 一个已经上架到租户层的 skill，创建者重新传一版不会把它
    偷偷降回私有，也不会因此绕过审核。
    """
    if not file.filename:
        raise HTTPException(400, "缺少文件名")
    if not file.filename.endswith((".zip", ".skill")):
        raise HTTPException(400, "仅支持 .zip 或 .skill 格式")

    raw = file.file.read()
    if len(raw) > 500 * 1024 * 1024:
        raise HTTPException(413, "文件超过 500MB 限制")

    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise HTTPException(400, "无效的 zip 文件")

    skill_md_path = _find_skill_md(zf)
    if not skill_md_path:
        raise HTTPException(400, "zip 内未找到 SKILL.md")

    try:
        parsed = _parse_skill_md(zf.read(skill_md_path).decode("utf-8"))
    except ValueError as e:
        raise HTTPException(422, f"SKILL.md 解析失败: {e}")
    except UnicodeDecodeError:
        raise HTTPException(422, "SKILL.md 不是 UTF-8 编码")

    version = version.strip().removeprefix("v")
    category = category.strip() or "general"
    if not re.match(r"^\d+(?:\.\d+){0,2}(?:[-+][0-9A-Za-z.-]+)?$", version):
        raise HTTPException(422, "version 必须是语义化版本号，例如 1.2.0")
    existing = (
        db.query(Skill)
        .filter(Skill.tenant_id == actor.tenant_id, Skill.name == parsed["name"])
        .first()
    )
    if existing and not _can_edit(existing, actor):
        raise HTTPException(403, f"{parsed['name']!r} 是别人的 Skill，不能覆盖")
    if existing and db.query(SkillVersion).filter(
        SkillVersion.skill_id == existing.id,
        SkillVersion.version_num == version,
    ).first():
        raise HTTPException(409, f"Skill 版本 {version!r} 已存在")

    # sha 进对象 key ⇒ 包不可变 ⇒ 投递侧的缓存永久有效，不需要失效判断。
    # 见 lea-tools 的 skill_provisioner.ensure_cached。
    sha256 = hashlib.sha256(raw).hexdigest()
    obj_key = f"skills/{actor.tenant_id}/{parsed['name']}/{sha256}.zip"
    if _storage_backend() == "obs":
        from obs import PutObjectHeader

        client, bucket = _get_obs_client()
        response = client.putContent(
            bucket,
            obj_key,
            content=raw,
            headers=PutObjectHeader(
                contentType="application/zip",
                contentLength=len(raw),
            ),
        )
        if response.status >= 300:
            raise HTTPException(502, f"OBS 上传失败: {response.status}")
    else:
        client, bucket = _get_minio_client()
        client.put_object(
            Bucket=bucket, Key=obj_key, Body=raw, ContentType="application/zip"
        )
    bundle_uri = f"s3://{bucket}/{obj_key}"

    if existing:
        existing.description = parsed["description"]
        existing.category = category
        existing.system_prompt = parsed["system_prompt"]
        existing.required_tools = parsed["required_tools"]
        existing.version = version
        existing.bundle_uri = bundle_uri
        db.add(SkillVersion(
            skill_id=existing.id,
            version_num=version,
            snapshot=_version_snapshot(existing, version, bundle_uri),
            change_log=change_log.strip(),
            bundle_uri=bundle_uri,
            sha256=sha256,
            size_bytes=len(raw),
            created_by=actor.user_id,
        ))
        db.commit()
        db.refresh(existing)
        return _skill_to_dict(existing, actor)

    skill = Skill(
        tenant_id=actor.tenant_id,
        name=parsed["name"],
        display_name=parsed["name"].replace("-", " ").title(),
        description=parsed["description"],
        version=version,
        system_prompt=parsed["system_prompt"],
        required_tools=parsed["required_tools"],
        bundle_uri=bundle_uri,
        enabled=True,
        is_builtin=False,
        created_by=actor.user_id,
        owner_scope=OWNER_SCOPE_USER,
        owner_user_id=actor.user_id,
    )
    db.add(skill)
    db.commit()
    db.refresh(skill)
    db.add(SkillVersion(
        skill_id=skill.id,
        version_num=version,
        snapshot=_version_snapshot(skill, version, bundle_uri),
        change_log=change_log.strip(),
        bundle_uri=bundle_uri,
        sha256=sha256,
        size_bytes=len(raw),
        created_by=actor.user_id,
    ))
    db.commit()
    return _skill_to_dict(skill, actor)


@router.get("/{skill_id}/download")
def download_skill_bundle(
    skill_id: int,
    expires: int = Query(600, ge=60, le=3600, description="链接有效期（秒）"),
    version: str | None = Query(None, description="版本号；不填下载当前版本"),
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    """返回一个带时效的下载直链。

    给客户端用：它拿到 URL 之后自己去下，不经过这个服务转发 —— 一个 50MB 的包
    走 FastAPI 转发会把 worker 占满，而对象存储本来就擅长这件事。

    可见性走 `_load_visible`，所以拿不到别人私有 skill 的链接。这一层必须有：
    bundle_uri 里的 sha 是可枚举的，光靠"猜不到 key"不算保护。

    ⚠️ MINIO_PUBLIC_ENDPOINT：预签名 URL 里的 host 来自 boto3 的 endpoint_url，
    而那个值是**集群内**地址（默认 localhost:9000），客户端连不上。所以这里允许用
    MINIO_PUBLIC_ENDPOINT 覆盖成对外地址。没配的话就退回内部地址 —— 那在本机开发
    是对的，在生产会给出一个客户端打不开的链接，所以部署时必须配。
    """
    skill = _load_visible(db, actor, skill_id)
    release = None
    if version:
        release = db.query(SkillVersion).filter(
            SkillVersion.skill_id == skill.id,
            SkillVersion.version_num == version,
        ).first()
        if release is None:
            raise HTTPException(404, "Skill 版本不存在")
    bundle_uri = release.bundle_uri if release else skill.bundle_uri
    if not bundle_uri:
        raise HTTPException(404, "这个 Skill 没有 bundle（纯 prompt 型）")
    if not bundle_uri.startswith("s3://"):
        raise HTTPException(500, f"bundle_uri 格式无法识别: {bundle_uri}")

    _, _, rest = bundle_uri.partition("s3://")
    bucket, _, key = rest.partition("/")
    if not bucket or not key:
        raise HTTPException(500, f"bundle_uri 解析失败: {skill.bundle_uri}")

    if _storage_backend() == "obs":
        client, _ = _get_obs_client()
        response = client.createSignedUrl(
            "GET", bucketName=bucket, objectKey=key, expires=expires
        )
        status = getattr(response, "status", None)
        url = getattr(response, "signedUrl", None)
        if (isinstance(status, int) and status >= 300) or not url:
            raise HTTPException(502, f"OBS 生成下载地址失败: {status or 'unknown'}")
    else:
        client, _ = _get_minio_client()
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires,
        )

    public = os.getenv("OBS_PUBLIC_ENDPOINT", "").strip().rstrip("/")
    if public:
        internal = os.getenv("OBS_ENDPOINT", "http://localhost:9000").rstrip("/")
        url = url.replace(internal, public, 1)

    return {
        "url": url,
        "expires_in": expires,
        "filename": f"{skill.name}-{version or skill.version}.zip",
        "sha256": key.rsplit("/", 1)[-1].removesuffix(".zip"),
    }
