"""kb_search tool 的 agent 白名单行为。

用 monkeypatch 替换 `search_knowledge_base` 和 `get_stream_writer`，
避免真正连 Milvus 和 langgraph runtime。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _mock_writer(monkeypatch):
    """langgraph.config.get_stream_writer 在 runtime 外调用会抛，替成 no-op。"""
    import lea_graph.tools.knowledge as knowledge_mod

    events: list[dict] = []

    def _fake_get_stream_writer():
        return lambda payload: events.append(payload)

    monkeypatch.setattr(knowledge_mod, "get_stream_writer", _fake_get_stream_writer)
    return events


async def test_kb_search_with_agent_kb_ids_passes_whitelist(monkeypatch):
    """agent_kb_ids 非空 → search_knowledge_base 收到 kb_ids 白名单参数。"""
    import lea_graph.tools.knowledge as knowledge_mod

    captured: dict = {}

    async def _fake_search(query, limit, **kwargs):
        captured["query"] = query
        captured["limit"] = limit
        captured["kb_ids"] = kwargs.get("kb_ids")
        return [{"content": "hit", "title": "t1", "score": 0.9, "kb_id": 3}]

    monkeypatch.setattr(knowledge_mod, "search_knowledge_base", _fake_search)

    result = await knowledge_mod.kb_search.ainvoke(
        {"query": "深度学习"},
        config={"configurable": {"agent_kb_ids": [3, 5]}},
    )

    assert captured["query"] == "深度学习"
    assert captured["kb_ids"] == [3, 5]
    assert "hit" in result


async def test_kb_search_with_empty_agent_kb_ids_returns_hint(monkeypatch):
    """agent_kb_ids=[] → 明确返回未配置提示，不调 Milvus。"""
    import lea_graph.tools.knowledge as knowledge_mod

    called = False

    async def _fake_search(*args, **kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(knowledge_mod, "search_knowledge_base", _fake_search)

    result = await knowledge_mod.kb_search.ainvoke(
        {"query": "xxx"},
        config={"configurable": {"agent_kb_ids": []}},
    )

    assert called is False, "empty agent_kb_ids 不应该调 search_knowledge_base"
    assert "未配置知识库" in result


async def test_kb_search_explicit_kb_ids_overrides_agent_binding(monkeypatch):
    """config.kb_ids 显式传 → 优先级最高，覆盖 agent_kb_ids（智能笔记场景）。"""
    import lea_graph.tools.knowledge as knowledge_mod

    captured: dict = {}

    async def _fake_search(query, limit, **kwargs):
        captured["kb_ids"] = kwargs.get("kb_ids")
        return []

    monkeypatch.setattr(knowledge_mod, "search_knowledge_base", _fake_search)

    await knowledge_mod.kb_search.ainvoke(
        {"query": "x"},
        config={
            "configurable": {
                "kb_ids": [99],  # 显式指定
                "agent_kb_ids": [1, 2, 3],  # 应被忽略
            }
        },
    )

    assert captured["kb_ids"] == [99]


async def test_kb_search_no_agent_no_kb_falls_back_to_tenant(monkeypatch):
    """agent_kb_ids 未提供 → 走 tenant/user 兜底（不返回未配置提示）。"""
    import lea_graph.tools.knowledge as knowledge_mod

    async def _fake_search(query, limit, **kwargs):
        return []

    monkeypatch.setattr(knowledge_mod, "search_knowledge_base", _fake_search)

    result = await knowledge_mod.kb_search.ainvoke(
        {"query": "x"},
        config={"configurable": {}},  # 完全没 agent_kb_ids
    )

    # 兜底路径：空结果就是"未找到相关文献"，不是"未配置知识库"
    assert "未配置知识库" not in result
