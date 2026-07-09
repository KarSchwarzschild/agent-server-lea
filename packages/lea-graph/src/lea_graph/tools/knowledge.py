"""公共 Tool: 知识库搜索。

优先级链（从高到低）：
    1. config["kb_ids"]         智能笔记/单文档场景显式指定（保留能力）
    2. config["agent_kb_ids"]   agent 侧权限白名单（Ph1 · Skill v2）
    3. tenant/user 可见 KB       兜底（旧行为，Ph1 迁移期保留）

未来 KB 服务化替换点（→ 独立 lea-kb-service HTTP 服务后）：
    只需替换 `search_knowledge_base(...)` 一处调用为 httpx.AsyncClient
    调用远端 POST /v1/search；其余 SSE 事件 / 参数处理保持不变。
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.config import get_stream_writer

from lea_graph.services.knowledge_base import search_knowledge_base


_NO_KB_HINT = (
    "该助手未配置知识库权限，无法执行知识检索。"
    "如需检索文献，请联系管理员将知识库挂到当前 Agent 上。"
)


@tool
async def kb_search(query: str, limit: int = 5, *, config: RunnableConfig = None) -> str:
    """搜索知识库中的文献资料。根据查询关键词从当前 Agent 已绑定的知识库中检索。"""
    writer = get_stream_writer()
    writer({"type": "action", "tool": "kb-search", "input": {"query": query, "limit": limit}})

    kb_ids: list[int] | None = None
    document_ids: list[int] | None = None
    agent_kb_ids: list[int] | None = None
    tenant_id = ""
    user_id = ""

    if config and config.get("configurable", {}):
        cfg = config["configurable"]
        kb_ids = cfg.get("kb_ids")
        document_ids = cfg.get("document_ids")
        agent_kb_ids = cfg.get("agent_kb_ids")

    # 优先级：显式 kb_ids > agent 绑定白名单 > tenant/user 兜底
    if not kb_ids and not document_ids:
        if agent_kb_ids is not None:
            # agent_id 已解析场景 —— 严格走白名单
            if not agent_kb_ids:
                writer({"type": "observation", "tool": "kb-search", "status": "no_kb_bound", "count": 0})
                return _NO_KB_HINT
            kb_ids = agent_kb_ids
        else:
            # 老兜底：tenant/user 可见 KB（例如智能笔记直调、无 agent 上下文的场景）
            try:
                from lea_server.core.tenant import get_tenant
                ctx = get_tenant()
                tenant_id = ctx.tenant_id
                user_id = ctx.extra.get("user_id", "")
            except Exception:
                pass

    results = await search_knowledge_base(
        query, limit,
        kb_ids=kb_ids,
        document_ids=document_ids,
        tenant_id=tenant_id,
        user_id=user_id,
    )

    if results:
        writer({
            "type": "references",
            "references": [
                {
                    "index": i,
                    "title": r.get("title") or f"文档 {r.get('document_id') or '?'}",
                    "score": round(float(r.get("score", 0.0)), 4),
                    "kb_id": r.get("kb_id"),
                    "document_id": r.get("document_id"),
                    "page": r.get("page"),
                    "snippet": (r.get("content") or "")[:200],
                }
                for i, r in enumerate(results, 1)
            ],
            "mode": "append",
        })

    writer({"type": "observation", "tool": "kb-search", "status": "success", "count": len(results)})

    if not results:
        return "未找到相关文献。"

    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "未知标题")
        content = r.get("content", "")[:300]
        lines.append(f"[{i}] {title}\n{content}")

    return "\n\n".join(lines)
