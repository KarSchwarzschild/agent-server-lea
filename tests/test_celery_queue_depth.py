"""retrieval-worker 的队列深度采样。

这个 gauge 是那个 worker 目前**唯一**的可观测性入口：它的 healthcheck 是
disable 的、进程里没有任何埋点，挂掉的表现是上传接口照常返回
status="processing"、文档永远停在 processing、知识库搜不到内容，而面板全绿。

所以这里盯两件最容易写错、且写错了本地完全看不出来的事：
  1. Redis 读不到时**不能**把 gauge 写成 0 —— 那会让"worker 挂了"和"队列正常
     清空"长得一模一样，正好抹掉这个指标存在的理由。
  2. 开了优先级之后队列会被 kombu 拆成多个 Redis key，只量基础 key 会少报积压。
"""
from __future__ import annotations

import asyncio

import pytest
from lea_rag import metrics


class FakeRedis:
    """只实现 llen 的假客户端。lengths 里没列的 key 当作不存在（返回 0）。"""

    def __init__(self, lengths: dict[str, int], fail: bool = False):
        self.lengths = lengths
        self.fail = fail
        self.queried: list[str] = []

    async def llen(self, key: str) -> int:
        self.queried.append(key)
        if self.fail:
            raise ConnectionError("redis is down")
        return self.lengths.get(key, 0)


def _gauge_value(queue: str = "retrieval"):
    return metrics.celery_queue_depth.labels(queue=queue)._value.get()


def test_sums_base_key(monkeypatch):
    fake = FakeRedis({"retrieval": 7})
    monkeypatch.setattr("lea_rag.services.cache.get_redis", lambda: fake)

    got = asyncio.run(metrics.sample_celery_queue_depth())

    assert got == 7
    assert _gauge_value() == 7


def test_sums_priority_shards(monkeypatch):
    """开优先级后一个逻辑队列散在多个 key 上，必须全部加起来。"""
    sep = "\x06\x16"
    fake = FakeRedis({
        "retrieval": 1,
        f"retrieval{sep}3": 2,
        f"retrieval{sep}6": 4,
        f"retrieval{sep}9": 8,
    })
    monkeypatch.setattr("lea_rag.services.cache.get_redis", lambda: fake)

    got = asyncio.run(metrics.sample_celery_queue_depth())

    assert got == 15, f"只量了部分分片，得到 {got}，应该是 1+2+4+8"


def test_redis_unavailable_returns_none_and_leaves_gauge_alone(monkeypatch):
    """Redis 没配时返回 None，且**不能**把 gauge 刷成 0。"""
    metrics.celery_queue_depth.labels(queue="retrieval").set(42)
    monkeypatch.setattr("lea_rag.services.cache.get_redis", lambda: None)

    got = asyncio.run(metrics.sample_celery_queue_depth())

    assert got is None
    assert _gauge_value() == 42, "没量到却动了 gauge —— 0 会被当成'队列空了'"


def test_redis_error_returns_none_and_leaves_gauge_alone(monkeypatch):
    """broker 读失败同理：返回 None，不要用 0 冒充'队列是空的'。"""
    metrics.celery_queue_depth.labels(queue="retrieval").set(99)
    fake = FakeRedis({}, fail=True)
    monkeypatch.setattr("lea_rag.services.cache.get_redis", lambda: fake)

    got = asyncio.run(metrics.sample_celery_queue_depth())

    assert got is None
    assert _gauge_value() == 99


def test_empty_queue_is_zero_not_none(monkeypatch):
    """队列真的空了要记 0 —— 这跟"没量到"必须能区分开。"""
    fake = FakeRedis({})
    monkeypatch.setattr("lea_rag.services.cache.get_redis", lambda: fake)

    got = asyncio.run(metrics.sample_celery_queue_depth())

    assert got == 0
    assert _gauge_value() == 0


@pytest.mark.parametrize("queue", ["retrieval", "other"])
def test_queue_name_is_a_label(monkeypatch, queue):
    fake = FakeRedis({queue: 3})
    monkeypatch.setattr("lea_rag.services.cache.get_redis", lambda: fake)

    asyncio.run(metrics.sample_celery_queue_depth(queue))

    assert _gauge_value(queue) == 3
    assert fake.queried[0] == queue
