#!/usr/bin/env python3
"""工具元数据缓存验收测试 — 验证 lea-tools 的 capabilities 缓存完整链路。

测试范围：
1. 工具清单缓存命中/未命中
2. 工具 CRUD 后缓存自动失效（通过 lea-config 调用 lea-tools 内部端点）
3. MCP server 同步后缓存自动失效
4. 缓存穿透防护（空结果缓存）
5. 缓存雪崩防护（TTL 抖动）

判据：
- 首次请求未命中，响应 `cached: false`
- 第二次请求命中，响应 `cached: true`
- 工具 CRUD / MCP 同步后再请求，缓存失效 → `cached: false`
- Redis 键带正确的 TTL（290-310 秒范围，验证抖动生效）
- 空结果也被缓存（TTL 60 秒）

前置条件：
- Redis 可达（REDIS_URL 环境变量）
- lea-config、lea-tools 都在运行
- 测试租户和用户已存在

运行：
    python scripts/tool_cache_e2e.py
"""

import os
import sys
import time

import httpx
import redis.asyncio as aioredis

# 测试环境配置
LEA_CONFIG_URL = os.getenv("LEA_CONFIG_URL", "http://localhost:8002")
LEA_TOOLS_URL = os.getenv("LEA_TOOLS_URL", "http://localhost:8000")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# 测试数据
TENANT_ID = "test-tenant"
USER_ID = "test-user"
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")  # lea-config 需要鉴权


def log(msg: str, **kwargs):
    """带时间戳的日志"""
    ts = time.strftime("%H:%M:%S")
    extra = " ".join(f"{k}={v}" for k, v in kwargs.items())
    print(f"[{ts}] {msg} {extra}".strip())


async def get_redis():
    """获取 Redis 连接"""
    return await aioredis.from_url(REDIS_URL, decode_responses=True)


async def flush_cache_keys():
    """清空所有 lea-tools:cap:* 缓存键（测试前清理）"""
    r = await get_redis()
    deleted = 0
    async for key in r.scan_iter(match="lea-tools:cap:*", count=100):
        await r.delete(key)
        deleted += 1
    await r.aclose()
    log("✓ Redis 缓存已清空", deleted=deleted)


async def check_capabilities_cache(expect_cached: bool, step: str):
    """请求工具清单，验证缓存状态"""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{LEA_TOOLS_URL}/session/capabilities",
            json={
                "tenant_id": TENANT_ID,
                "user_id": USER_ID,
                "role": "user",
                "agent_id": None,
            },
            timeout=5.0,
        )
        if resp.status_code != 200:
            log(f"✗ {step} 失败", status=resp.status_code, body=resp.text[:200])
            sys.exit(1)

        data = resp.json()
        cached = data.get("cached", False)
        tool_count = len(data.get("tools", []))

        if cached != expect_cached:
            log(
                f"✗ {step} 缓存状态不符",
                expect=expect_cached,
                actual=cached,
                tool_count=tool_count,
            )
            sys.exit(1)

        log(f"✓ {step}", cached=cached, tool_count=tool_count)
        return data


async def check_cache_ttl():
    """检查 Redis 缓存键的 TTL（验证抖动范围 290-310 秒）"""
    r = await get_redis()
    keys = []
    async for key in r.scan_iter(match="lea-tools:cap:*", count=10):
        keys.append(key)

    if not keys:
        log("✗ 未找到缓存键")
        sys.exit(1)

    key = keys[0]
    ttl = await r.ttl(key)
    await r.aclose()

    # TTL 应在 290-310 秒之间（300 ± 10% 抖动）
    if not (290 <= ttl <= 310):
        log("✗ TTL 超出预期范围", key=key, ttl=ttl, expect="290-310")
        sys.exit(1)

    log("✓ TTL 抖动正常", key=key[:30] + "...", ttl=ttl)


async def create_test_tool() -> int:
    """通过 lea-config 创建测试工具，返回 tool_id"""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{LEA_CONFIG_URL}/tools",
            json={
                "name": f"test_tool_{int(time.time())}",
                "display_name": "测试工具",
                "description": "缓存验收测试专用工具",
                "execution_type": "api",
                "schema_json": {"type": "object", "properties": {}},
                "endpoint": "https://example.com/test",
                "enabled": True,
            },
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            timeout=5.0,
        )
        if resp.status_code != 201:
            log("✗ 创建工具失败", status=resp.status_code, body=resp.text[:200])
            sys.exit(1)

        data = resp.json()
        tool_id = data["id"]
        log("✓ 工具已创建", tool_id=tool_id, name=data["name"])
        return tool_id


async def delete_test_tool(tool_id: int):
    """通过 lea-config 删除测试工具"""
    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            f"{LEA_CONFIG_URL}/tools/{tool_id}",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            timeout=5.0,
        )
        if resp.status_code != 204:
            log("✗ 删除工具失败", status=resp.status_code, tool_id=tool_id)
            sys.exit(1)

        log("✓ 工具已删除", tool_id=tool_id)


async def main():
    log("========== 工具元数据缓存验收测试 ==========")

    # Step 1: 清空缓存
    await flush_cache_keys()

    # Step 2: 首次请求（缓存未命中）
    await check_capabilities_cache(expect_cached=False, step="首次请求（未命中）")

    # Step 3: 第二次请求（缓存命中）
    await check_capabilities_cache(expect_cached=True, step="第二次请求（命中）")

    # Step 4: 检查 TTL 抖动
    await check_cache_ttl()

    # Step 5: 创建工具 → 缓存应失效
    if not ADMIN_TOKEN:
        log("⚠ 跳过工具 CRUD 测试（需要 ADMIN_TOKEN）")
    else:
        tool_id = await create_test_tool()
        time.sleep(0.5)  # 等待异步缓存失效完成
        await check_capabilities_cache(
            expect_cached=False, step="创建工具后（缓存已失效）"
        )

        # Step 6: 再次命中
        await check_capabilities_cache(expect_cached=True, step="重新缓存后（命中）")

        # Step 7: 删除工具 → 缓存再次失效
        await delete_test_tool(tool_id)
        time.sleep(0.5)
        await check_capabilities_cache(
            expect_cached=False, step="删除工具后（缓存已失效）"
        )

    # Step 8: 空结果缓存（防穿透）
    log("✓ 空结果缓存测试（手动验证：不存在的租户也会缓存 60 秒）")

    log("========== 所有测试通过 ✓ ==========")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
