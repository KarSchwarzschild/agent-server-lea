"""可选聊天模型列表 — 给前端的模型选择器。

为什么需要这个端点
──────────────────────────────────────────────────────────────────

`agents.model` 是一个**自由字符串**，没有枚举也没有校验。前端因此只能让用户
手打模型名，而打错的后果是静默的：`internal.py` 拿这个名字去
`llm_providers.models` 里找 provider，找不到就**回落到默认 provider**
（internal.py:271-272），不报错。用户看到的现象是"这个专家答得不太对劲"，
而不是"模型名写错了" —— 一个几乎不可能被归因到拼写的症状。

枚举其实一直存在，就在 `llm_providers.models` 那一列（逗号分隔），只是从来
没有任何路由把它露出来。这个端点就是把它露出来，别的什么都不做。

安全边界
──────────────────────────────────────────────────────────────────

**只返回模型名，绝不返回 base_url / api_key。** provider 行里装着租户的
LLM 凭据，这也是为什么不能简单地给 `llm_providers` 加一套 CRUD 了事 ——
那样任何普通用户都能读到 api_key。这里只投影出"能选哪些名字"。

权限是 `require_actor`（登录即可）而不是 `require_admin`：普通用户要给自己
建的专家挑模型，管它要管理员就等于这个选择器不存在。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from lea_authkit import Actor, require_actor
from lea_config.database import get_db
from lea_config.models.llm_provider import (
    LLMProvider,
    models_of,
    tenant_first,
    visible_to,
    window_of,
)

router = APIRouter(prefix="/llm-models", tags=["llm-models"])


@router.get("")
def list_chat_models(
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    """列出当前租户能用的聊天模型。

    ⚠️ 筛选判据必须跟 `internal.py` 解析 provider 时**一模一样**，所以两边都走
    models/llm_provider.py 里那一套 visible_to / tenant_first / models_of /
    window_of，不在这里重写条件。两边分叉的话，这里会列出运行时其实选不中的
    模型 —— 用户选了它，请求却悄悄走了默认 provider，比没有这个列表还糟。

    （原先两边各写一遍"tenant_id 严格相等"，于是也一起错了同一处：非 default
    租户看到空列表。靠注释约束同形状不够，判据抽成函数才真的锁住。）

    context_window 为 0 表示 provider 没配这个模型的窗口，lea-agent 会走
    保守兜底（32k）。原样透出 0 而不是塞个缺省值：两边各有一个缺省，它们迟早
    不一样。
    """
    providers = tenant_first(
        db.query(LLMProvider).filter(*visible_to(actor.tenant_id)).all(),
        actor.tenant_id,
    )

    # 同名模型可能出现在多个 provider 里。按名字去重，保留**先匹配到的那个**
    # —— internal.py 的匹配是 `for p in providers: ... break`，取的也是第一个，
    # 且两边的顺序都由 tenant_first 定（本租户优先于 default）。这里若改成
    # "后者覆盖前者"，列表显示的 provider 就会跟实际调用的不是同一个。
    seen: dict[str, dict] = {}
    for p in providers:
        for name in models_of(p):
            if name in seen:
                continue
            seen[name] = {
                "name": name,
                "provider": p.name,
                "is_default": bool(p.is_default),
                "context_window": window_of(p, name),
            }

    return {"items": sorted(seen.values(), key=lambda m: m["name"])}
