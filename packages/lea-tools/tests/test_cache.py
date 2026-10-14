"""工具元数据缓存单元测试"""

import pytest
from lea_tools.services.cache import (
    CACHE_TTL_CAPABILITIES,
    CACHE_TTL_NULL,
    _ttl_with_jitter,
    get_cached_capabilities,
    invalidate_capabilities,
    set_cached_capabilities,
)


@pytest.mark.asyncio
async def test_capabilities_cache_miss():
    """首次请求缓存未命中"""
    cached = await get_cached_capabilities("tenant1", "user1", "user", None)
    assert cached is None


@pytest.mark.asyncio
async def test_capabilities_cache_hit():
    """写入后能读出"""
    tools = [{"name": "test_tool", "description": "测试工具"}]
    await set_cached_capabilities("tenant2", "user2", "user", None, tools)

    cached = await get_cached_capabilities("tenant2", "user2", "user", None)
    assert cached == tools


@pytest.mark.asyncio
async def test_capabilities_cache_empty_list():
    """空列表缓存（防穿透）"""
    await set_cached_capabilities("tenant3", "user3", "user", None, [])

    cached = await get_cached_capabilities("tenant3", "user3", "user", None)
    assert cached == []


@pytest.mark.asyncio
async def test_capabilities_cache_invalidation():
    """缓存失效后读不到"""
    tools = [{"name": "tool1"}]
    await set_cached_capabilities("tenant4", "user4", "user", None, tools)

    # 确认能读到
    cached = await get_cached_capabilities("tenant4", "user4", "user", None)
    assert cached == tools

    # 清除缓存
    await invalidate_capabilities("tenant4")

    # 读不到了
    cached = await get_cached_capabilities("tenant4", "user4", "user", None)
    assert cached is None


@pytest.mark.asyncio
async def test_capabilities_cache_key_isolation():
    """不同用户的缓存隔离"""
    tools_alice = [{"name": "alice_tool"}]
    tools_bob = [{"name": "bob_tool"}]

    await set_cached_capabilities("tenant5", "alice", "user", None, tools_alice)
    await set_cached_capabilities("tenant5", "bob", "user", None, tools_bob)

    # alice 读到的是自己的
    cached_alice = await get_cached_capabilities("tenant5", "alice", "user", None)
    assert cached_alice == tools_alice

    # bob 读到的是自己的
    cached_bob = await get_cached_capabilities("tenant5", "bob", "user", None)
    assert cached_bob == tools_bob


def test_ttl_jitter_range():
    """TTL 抖动在 ±10% 范围内"""
    base_ttl = 300
    for _ in range(100):
        actual = _ttl_with_jitter(base_ttl)
        assert 270 <= actual <= 330, f"TTL {actual} 超出范围 [270, 330]"


def test_ttl_constants():
    """TTL 常量配置正确"""
    assert CACHE_TTL_CAPABILITIES == 300  # 5 分钟
    assert CACHE_TTL_NULL == 60  # 1 分钟
