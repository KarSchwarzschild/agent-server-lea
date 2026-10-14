"""可选检索模型列表 — 给前端的 embedding/reranker/vlm 模型选择器。

跟 llm_models.py 是同一个问题、同一个修法：`RetrievalModel` 注册表已经存在
（见 api/models.py），但那套接口全部 `require_admin`。知识库建库/改库表单
（server_user_kb.py）此前只能让用户自由填一个模型名字符串 —— 打错字的后果
跟 llm_models.py 文档里点出的那个坏体验一样：静默落到某个默认模型，用户看到
的症状是"检索效果不对"，而不是"模型名写错了"。

安全边界
──────────────────────────────────────────────────────────────────
**只返回 name / model_type / model_name / dimension / is_default。**
`api/models.py` 的 admin 投影（`_to_dict`）里带着 `api_base`（虽然不带
`api_key`）——那个字段对 admin 是可以的，但这里是任何登录用户都能读的端点，
不能沿用同一套投影，得单独写一遍更严格的字段列表，否则以后 `api/models.py`
的 `_to_dict` 加字段时这边会被动带出新的敏感信息。

权限是 `require_actor`（登录即可）而不是 `require_admin`：普通用户建知识库
时要挑 embedding 模型，管它要管理员就等于这个选择器不存在，参见 llm_models.py。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from lea_authkit import Actor, require_actor
from sqlalchemy.orm import Session

from lea_config.database import get_db
from lea_config.models.retrieval_model import RetrievalModel

router = APIRouter(prefix="/retrieval-models", tags=["retrieval-models"])

_ALLOWED_TYPES = ("embedding", "reranker", "vlm", "asr")


@router.get("")
def list_retrieval_models(
    model_type: str | None = Query(None, pattern=r"^(embedding|reranker|vlm|asr)$"),
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    """列出可用的检索模型，只投影安全字段。

    `RetrievalModel` 是全局共享注册表（无 tenant_id），所以这里不按租户过滤 ——
    跟 api/models.py 的 admin 列表一致，只是多了 enabled 过滤（普通用户不该
    选到一个已经被管理员停用的模型）。
    """
    q = db.query(RetrievalModel).filter(RetrievalModel.enabled == True)  # noqa: E712
    if model_type:
        q = q.filter(RetrievalModel.model_type == model_type)
    rows = q.order_by(RetrievalModel.model_type, RetrievalModel.name).all()
    return {
        "items": [
            {
                "name": m.name,
                "model_type": m.model_type,
                "model_name": m.model_name or "",
                "dimension": m.dimension,
                "is_default": bool(m.is_default),
            }
            for m in rows
        ]
    }
