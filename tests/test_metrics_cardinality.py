"""endpoint 标签必须是路由模板，不能是实际路径。

为什么单独一个文件盯这件事：原来两个服务的 MetricsMiddleware 都用
`request.url.path` 当 endpoint 标签，于是每个不同的 URL 都是一条独立时间序列。
路径里带的是用户生成的主键（/memories/50、/agents/123），所以序列数没有上限。

这个坏法的特点是**本地完全看不出来**：功能正常、测试全绿、指标也"有数据"。
代价要等 Prometheus 跑一段时间、内存按序列数涨起来才显形，而那时候已经在生产了。
所以它需要一条断言明确挡住。

另一半是安全性：回退到真实路径的话，拿一串随机路径打过来就能把序列数顶上去 ——
等于把监控系统的内存变成一个远程可打的靶子。所以匹配不上路由时必须归成固定值，
测试里有一条专门盯这个。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lea_config.metrics import _endpoint_label as config_endpoint_label
from lea_rag.metrics import _endpoint_label as rag_endpoint_label


def _app_capturing(label_fn):
    """起一个最小 app，把中间件算出来的 endpoint 标签记下来。"""
    app = FastAPI()
    seen: list[str] = []

    @app.middleware("http")
    async def capture(request, call_next):
        response = await call_next(request)
        seen.append(label_fn(request))
        return response

    @app.get("/memories/{memory_id}")
    def one(memory_id: int):
        return {"id": memory_id}

    @app.get("/kb/{kb_id}/documents/{doc_id}")
    def nested(kb_id: int, doc_id: int):
        return {"kb": kb_id, "doc": doc_id}

    return app, seen


@pytest.mark.parametrize(
    "label_fn", [config_endpoint_label, rag_endpoint_label],
    ids=["lea-config", "lea-rag"],
)
def test_distinct_ids_collapse_to_one_series(label_fn):
    """同一个端点、不同 ID —— 必须是同一个标签值。

    这是基数爆炸的直接判据：打 50 个不同 ID，标签值只能有一种。
    """
    app, seen = _app_capturing(label_fn)
    client = TestClient(app)

    for memory_id in range(50):
        assert client.get(f"/memories/{memory_id}").status_code == 200

    assert set(seen) == {"/memories/{memory_id}"}, (
        f"50 个不同 ID 产生了 {len(set(seen))} 种标签值，序列数会随数据量无上限增长"
    )


@pytest.mark.parametrize(
    "label_fn", [config_endpoint_label, rag_endpoint_label],
    ids=["lea-config", "lea-rag"],
)
def test_nested_path_params_also_collapse(label_fn):
    """多段路径参数同样要折叠。嵌套路由是 /kb/1/documents/2 这种，两个参数都得模板化。"""
    app, seen = _app_capturing(label_fn)
    client = TestClient(app)

    client.get("/kb/1/documents/2")
    client.get("/kb/3/documents/4")

    assert set(seen) == {"/kb/{kb_id}/documents/{doc_id}"}


@pytest.mark.parametrize(
    "label_fn", [config_endpoint_label, rag_endpoint_label],
    ids=["lea-config", "lea-rag"],
)
def test_unmatched_paths_share_one_label(label_fn):
    """匹配不上路由的请求必须归成同一个固定标签。

    **不能**回退到 request.url.path：那样一个扫描器（或者任何人手敲一串随机 URL）
    就能让序列数跟着请求数涨，监控内存成了远程可打的靶子。这条用例就是拦这个。
    """
    app, seen = _app_capturing(label_fn)
    client = TestClient(app)

    for probe in ("/nope", "/also-nope", "/../../etc/passwd", "/random-" + "x" * 40):
        client.get(probe)

    assert set(seen) == {"unmatched"}, (
        f"未匹配的路径产生了 {len(set(seen))} 种标签值：{sorted(set(seen))}"
    )
