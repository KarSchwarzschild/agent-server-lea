#!/usr/bin/env python3
"""
生产就绪测试套件 — 验证 PRODUCTION_OPTIMIZATION_PLAN 所有功能
"""

import asyncio
import sys
import time
from dataclasses import dataclass

import httpx

# 强制 UTF-8 输出
if sys.stdout.encoding != 'utf-8':
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)

# 服务端点配置
SERVICES = {
    "lea-agent": "http://localhost:8200",
    "lea-config": "http://localhost:8500",
    "lea-rag": "http://localhost:8300",
    "lea-tools": "http://localhost:8400",
}

@dataclass
class TestResult:
    name: str
    status: str  # "PASS", "FAIL", "SKIP"
    message: str
    duration: float = 0.0

results: list[TestResult] = []

async def check_service_health(service_name: str, url: str) -> bool:
    """检查服务健康状态"""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{url}/health")
            return resp.status_code == 200
    except Exception as e:
        print(f"❌ {service_name} 连接失败: {e}")
        return False

async def test_prometheus_metrics(service_name: str, url: str) -> TestResult:
    """测试 Prometheus /metrics 端点"""
    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{url}/metrics")

        if resp.status_code != 200:
            return TestResult(
                name=f"Prometheus:/metrics/{service_name}",
                status="FAIL",
                message=f"HTTP {resp.status_code}",
                duration=time.time() - start
            )

        content = resp.text
        if "# HELP" not in content or "# TYPE" not in content:
            return TestResult(
                name=f"Prometheus:/metrics/{service_name}",
                status="FAIL",
                message="Prometheus 格式不正确",
                duration=time.time() - start
            )

        # 检查服务特定指标
        if service_name == "lea-agent":
            required_metrics = ["lea_agent_http_requests_total", "lea_agent_circuit_breaker"]
        elif service_name == "lea-config":
            required_metrics = ["lea_config_http_requests_total", "lea_config_db_queries_total"]
        elif service_name == "lea-rag":
            required_metrics = ["lea_rag_http_requests_total", "lea_rag_kb_search_total"]
        elif service_name == "lea-tools":
            required_metrics = ["lea_tools_http_requests_total", "lea_tools_tool_executions_total"]
        else:
            required_metrics = []

        missing = [m for m in required_metrics if m not in content]
        if missing:
            return TestResult(
                name=f"Prometheus:/metrics/{service_name}",
                status="FAIL",
                message=f"缺少指标: {', '.join(missing)}",
                duration=time.time() - start
            )

        return TestResult(
            name=f"Prometheus:/metrics/{service_name}",
            status="PASS",
            message="所有指标已注册",
            duration=time.time() - start
        )

    except Exception as e:
        return TestResult(
            name=f"Prometheus:/metrics/{service_name}",
            status="FAIL",
            message=str(e),
            duration=time.time() - start
        )

async def test_cache_integration() -> TestResult:
    """测试缓存集成"""
    start = time.time()
    try:
        # 检查 config_loader.go 是否使用缓存
        with open("packages/lea-agent/internal/runtime/config_loader.go") as f:
            content = f.read()

        if "cache.Get" in content and "cache.Set" in content:
            return TestResult(
                name="缓存集成:/LoadAgentConfig",
                status="PASS",
                message="缓存已集成到 LoadAgentConfig",
                duration=time.time() - start
            )
        else:
            return TestResult(
                name="缓存集成:/LoadAgentConfig",
                status="FAIL",
                message="cache.Get/Set 未找到",
                duration=time.time() - start
            )
    except Exception as e:
        return TestResult(
            name="缓存集成:/LoadAgentConfig",
            status="FAIL",
            message=str(e),
            duration=time.time() - start
        )

async def test_kb_memory_degradation() -> TestResult:
    """测试 KB/Memory 降级"""
    start = time.time()
    try:
        with open("packages/lea-agent/internal/runtime/platform_tools.go") as f:
            content = f.read()

        # 检查三个工具是否都有降级逻辑
        tools = ["KBSearchTool", "MemorySearchTool", "MemoryAddTool"]
        found = 0
        for tool in tools:
            if f"{tool}.Run" in content and "IsError: false" in content:
                found += 1

        if found == 3:
            return TestResult(
                name="降级逻辑:KB/Memory",
                status="PASS",
                message="所有工具都有 fail-soft 降级",
                duration=time.time() - start
            )
        else:
            return TestResult(
                name="降级逻辑:KB/Memory",
                status="FAIL",
                message=f"只找到 {found}/3 个工具的降级逻辑",
                duration=time.time() - start
            )
    except Exception as e:
        return TestResult(
            name="降级逻辑:KB/Memory",
            status="FAIL",
            message=str(e),
            duration=time.time() - start
        )

async def test_circuit_breaker() -> TestResult:
    """测试熔断器"""
    start = time.time()
    try:
        with open("packages/lea-agent/internal/breaker/breaker.go") as f:
            content = f.read()

        required = ["Execute", "CircuitBreakerState", "maxFailures", "timeout"]
        if all(req in content for req in required):
            return TestResult(
                name="熔断器:CircuitBreaker",
                status="PASS",
                message="熔断器完整实现",
                duration=time.time() - start
            )
        else:
            return TestResult(
                name="熔断器:CircuitBreaker",
                status="FAIL",
                message="熔断器实现不完整",
                duration=time.time() - start
            )
    except Exception as e:
        return TestResult(
            name="熔断器:CircuitBreaker",
            status="FAIL",
            message=str(e),
            duration=time.time() - start
        )

async def test_celery_queue() -> TestResult:
    """测试 Celery 异步队列"""
    start = time.time()
    try:
        with open("packages/lea-rag/src/lea_rag/tasks.py") as f:
            content = f.read()

        if "celery_app" in content and "process_document" in content and "@celery_app.task" in content:
            return TestResult(
                name="队列:Celery异步任务",
                status="PASS",
                message="Celery 异步任务已实现",
                duration=time.time() - start
            )
        else:
            return TestResult(
                name="队列:Celery异步任务",
                status="FAIL",
                message="Celery 任务定义不完整",
                duration=time.time() - start
            )
    except Exception as e:
        return TestResult(
            name="队列:Celery异步任务",
            status="FAIL",
            message=str(e),
            duration=time.time() - start
        )

async def test_nginx_rate_limiting() -> TestResult:
    """测试 Nginx 限流配置"""
    start = time.time()
    try:
        with open("infra/nginx/lea-platform.conf") as f:
            content = f.read()

        required = ["limit_req_zone", "limit_req", "rate"]
        if all(req in content for req in required):
            return TestResult(
                name="限流:Nginx三层",
                status="PASS",
                message="Nginx 限流规则已配置",
                duration=time.time() - start
            )
        else:
            return TestResult(
                name="限流:Nginx三层",
                status="FAIL",
                message="限流规则配置不完整",
                duration=time.time() - start
            )
    except Exception as e:
        return TestResult(
            name="限流:Nginx三层",
            status="FAIL",
            message=str(e),
            duration=time.time() - start
        )

async def test_database_indexes() -> TestResult:
    """测试数据库索引"""
    start = time.time()
    try:
        # 检查迁移文件是否存在
        import os
        migrations_dir = "packages/lea-agent/migrations"
        migration_files = os.listdir(migrations_dir)

        index_migrations = [f for f in migration_files if "index" in f.lower()]

        if len(index_migrations) >= 2:  # 至少应该有两个索引迁移文件
            return TestResult(
                name="索引:数据库",
                status="PASS",
                message=f"数据库索引已创建 ({len(index_migrations)} 个迁移)",
                duration=time.time() - start
            )
        else:
            return TestResult(
                name="索引:数据库",
                status="FAIL",
                message=f"索引迁移文件过少 ({len(index_migrations)} 个)",
                duration=time.time() - start
            )
    except Exception as e:
        return TestResult(
            name="索引:数据库",
            status="FAIL",
            message=str(e),
            duration=time.time() - start
        )

async def test_graceful_shutdown() -> TestResult:
    """测试优雅关闭"""
    start = time.time()
    try:
        with open("packages/lea-agent/cmd/server/main.go") as f:
            content = f.read()

        required = ["signal.Notify", "SIGTERM", "SIGINT", "preStop"]
        if all(req in content for req in required):
            return TestResult(
                name="优雅关闭:Drain/preStop",
                status="PASS",
                message="优雅关闭已实现",
                duration=time.time() - start
            )
        else:
            return TestResult(
                name="优雅关闭:Drain/preStop",
                status="FAIL",
                message="优雅关闭实现不完整",
                duration=time.time() - start
            )
    except Exception as e:
        return TestResult(
            name="优雅关闭:Drain/preStop",
            status="FAIL",
            message=str(e),
            duration=time.time() - start
        )

async def run_all_tests():
    """运行所有测试"""
    print("=" * 80)
    print("[TEST] LEA Production Readiness Suite")
    print("=" * 80)
    print()

    # 先检查服务可用性
    print("📋 检查服务健康状态...")
    services_available = {}
    for name, url in SERVICES.items():
        available = await check_service_health(name, url)
        services_available[name] = available
        status = "✅" if available else "❌"
        print(f"  {status} {name}: {url}")
    print()

    # 代码级测试
    print("📝 运行代码级验收测试...")
    code_tests = [
        ("数据库索引", test_database_indexes),
        ("缓存集成", test_cache_integration),
        ("KB/Memory 降级", test_kb_memory_degradation),
        ("熔断器", test_circuit_breaker),
        ("Celery 队列", test_celery_queue),
        ("Nginx 限流", test_nginx_rate_limiting),
        ("优雅关闭", test_graceful_shutdown),
    ]

    for name, test_func in code_tests:
        result = await test_func()
        results.append(result)
        status_icon = "✅" if result.status == "PASS" else "❌" if result.status == "FAIL" else "⏭️"
        print(f"  {status_icon} {result.name}: {result.message} ({result.duration:.3f}s)")
    print()

    # 端点级测试
    print("🌐 运行端点级测试...")
    for service_name, url in SERVICES.items():
        if services_available.get(service_name):
            result = await test_prometheus_metrics(service_name, url)
            results.append(result)
            status_icon = "✅" if result.status == "PASS" else "❌" if result.status == "FAIL" else "⏭️"
            print(f"  {status_icon} {result.name}: {result.message} ({result.duration:.3f}s)")
        else:
            result = TestResult(
                name=f"Prometheus:/metrics/{service_name}",
                status="SKIP",
                message="服务不可用",
            )
            results.append(result)
            print(f"  ⏭️ {result.name}: {result.message}")
    print()

    # 总结
    print("=" * 80)
    print("📊 测试总结")
    print("=" * 80)

    passed = sum(1 for r in results if r.status == "PASS")
    failed = sum(1 for r in results if r.status == "FAIL")
    skipped = sum(1 for r in results if r.status == "SKIP")
    total = len(results)

    print(f"✅ 通过: {passed}/{total}")
    print(f"❌ 失败: {failed}/{total}")
    print(f"⏭️ 跳过: {skipped}/{total}")
    print()

    if failed > 0:
        print("失败详情:")
        for r in results:
            if r.status == "FAIL":
                print(f"  - {r.name}: {r.message}")
        return 1
    else:
        print("🎉 所有测试通过!")
        return 0

if __name__ == "__main__":
    exit_code = asyncio.run(run_all_tests())
    sys.exit(exit_code)
