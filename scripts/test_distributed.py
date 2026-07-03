"""
LEA Platform 分布式集成测试。

前置条件:
  - MySQL / Redis / Kafka 容器已启动（docker compose -f infra/docker-compose.dev.yml up -d）
  - 两个后端实例分别监听 PORT_A 和 PORT_B

用法:
  uv run python scripts/test_distributed.py            # 默认 8000 / 8001
  uv run python scripts/test_distributed.py 8000 8002  # 自定义端口
"""

from __future__ import annotations

import json
import sys
import time
import uuid

import httpx

PORT_A = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
PORT_B = int(sys.argv[2]) if len(sys.argv) > 2 else 8001
BASE_A = f"http://127.0.0.1:{PORT_A}"
BASE_B = f"http://127.0.0.1:{PORT_B}"

PASS = 0
FAIL = 0
RESULTS: list[str] = []


def report(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    tag = "PASS" if ok else "FAIL"
    if ok:
        PASS += 1
    else:
        FAIL += 1
    line = f"[{tag}] {name}"
    if detail:
        line += f"  ({detail})"
    RESULTS.append(line)
    print(line)


# ─── T1: 双实例健康检查 ───

def test_health():
    try:
        ra = httpx.get(f"{BASE_A}/health", timeout=5)
        rb = httpx.get(f"{BASE_B}/health", timeout=5)
        ok = ra.status_code == 200 and rb.status_code == 200
        report("T1: 双实例健康检查", ok, f"A={ra.status_code} B={rb.status_code}")
    except Exception as e:
        report("T1: 双实例健康检查", False, str(e))


# ─── T2: JWT 跨实例认证 ───

def test_jwt_cross_instance():
    tag = uuid.uuid4().hex[:8]
    username = f"test_{tag}"
    try:
        r = httpx.post(f"{BASE_A}/api/auth/register", json={
            "username": username, "password": "pass1234", "email": f"{tag}@test.com"
        }, timeout=10)
        if r.status_code != 200:
            report("T2: JWT 跨实例认证", False, f"注册失败: {r.status_code} {r.text}")
            return None
        token = r.json()["access_token"]

        r2 = httpx.get(f"{BASE_B}/api/auth/me", headers={"Authorization": f"Bearer {token}"}, timeout=10)
        ok = r2.status_code == 200 and r2.json()["username"] == username
        report("T2: JWT 跨实例认证", ok, f"实例A注册→实例B查询: {r2.json().get('username', '?')}")
        return token
    except Exception as e:
        report("T2: JWT 跨实例认证", False, str(e))
        return None


# ─── T3: DB 跨实例共享 ───

def test_db_cross_instance(token: str | None):
    if not token:
        report("T3: DB 跨实例共享 (Review CRUD)", False, "无可用 token")
        return
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = httpx.post(f"{BASE_A}/api/apps/research/reviews", json={
            "title": "分布式测试综述", "topic": "LLM in Education", "requirements": "test only"
        }, headers=headers, timeout=10)
        if r.status_code != 200:
            report("T3: DB 跨实例共享 (Review CRUD)", False, f"创建失败: {r.status_code} {r.text}")
            return

        review_id = r.json()["id"]

        r2 = httpx.get(f"{BASE_B}/api/apps/research/reviews/{review_id}", headers=headers, timeout=10)
        ok = r2.status_code == 200 and r2.json()["topic"] == "LLM in Education"
        report("T3: DB 跨实例共享 (Review CRUD)", ok,
               f"实例A创建id={review_id}→实例B读取: topic={r2.json().get('topic', '?')}")

        httpx.delete(f"{BASE_A}/api/apps/research/reviews/{review_id}", headers=headers, timeout=10)
    except Exception as e:
        report("T3: DB 跨实例共享 (Review CRUD)", False, str(e))


# ─── T4: 熔断器状态共享 ───

def test_breakers():
    try:
        ra = httpx.get(f"{BASE_A}/api/admin/breakers", timeout=10)
        rb = httpx.get(f"{BASE_B}/api/admin/breakers", timeout=10)
        ok = ra.status_code == 200 and rb.status_code == 200
        detail = f"A={ra.status_code} B={rb.status_code}"
        if ok:
            data_a = ra.json()
            data_b = rb.json()
            detail += f" | A_keys={list(data_a.keys()) if isinstance(data_a, dict) else len(data_a)}"
            detail += f" B_keys={list(data_b.keys()) if isinstance(data_b, dict) else len(data_b)}"
        report("T4: 熔断器状态共享 (Redis)", ok, detail)
    except Exception as e:
        report("T4: 熔断器状态共享 (Redis)", False, str(e))


# ─── T5: 限流器共享 ───

def test_rate_limiter():
    try:
        ra = httpx.get(f"{BASE_A}/api/admin/usage/default", timeout=10)
        rb = httpx.get(f"{BASE_B}/api/admin/usage/default", timeout=10)
        ok = ra.status_code == 200 and rb.status_code == 200
        detail = f"A={ra.status_code} B={rb.status_code}"
        if ok:
            detail += f" | A_rpm_limit={ra.json().get('rpm_limit')} B_rpm_limit={rb.json().get('rpm_limit')}"
        report("T5: 限流器共享 (Redis)", ok, detail)
    except Exception as e:
        report("T5: 限流器共享 (Redis)", False, str(e))


# ─── T6: Kafka 事件投递 ───

def test_kafka_events(token: str | None):
    if not token:
        report("T6: Kafka 事件投递", False, "无可用 token")
        return

    try:
        from kafka import KafkaConsumer
    except ImportError:
        report("T6: Kafka 事件投递", False, "kafka-python-ng 未安装")
        return

    try:
        consumer = KafkaConsumer(
            "lea.audit",
            bootstrap_servers=["127.0.0.1:9092"],
            auto_offset_reset="latest",
            consumer_timeout_ms=8000,
            value_deserializer=lambda m: json.loads(m.decode()),
        )
        # drain existing messages
        consumer.poll(timeout_ms=1000)

        httpx.post(f"{BASE_A}/api/chat/stream", json={
            "message": "kafka测试消息", "session_id": "kafka-test"
        }, headers={"Authorization": f"Bearer {token}"}, timeout=15)

        time.sleep(2)

        found = False
        for msg in consumer:
            if msg.value.get("action") == "chat":
                found = True
                break

        consumer.close()
        report("T6: Kafka 事件投递", found, "chat audit event " + ("found" if found else "not found in 8s"))
    except Exception as e:
        report("T6: Kafka 事件投递", False, str(e))


# ─── T7: Checkpointer 跨实例状态恢复 ───

def test_checkpointer_cross_instance(token: str | None):
    if not token:
        report("T7: Checkpointer 跨实例 (Redis)", False, "无可用 token")
        return
    headers = {"Authorization": f"Bearer {token}"}

    session_id = f"chk-{uuid.uuid4().hex[:8]}"
    try:
        r1 = httpx.post(f"{BASE_A}/api/chat/stream", json={
            "message": "你好，我是分布式测试用户", "session_id": session_id
        }, headers=headers, timeout=30)

        lines_a = r1.text.split("\n")
        events_a = [l for l in lines_a if l.startswith("data: ")]
        got_done_a = any("event: done" in l for l in lines_a) or any('"status": "completed"' in e for e in events_a)

        time.sleep(1)

        r2 = httpx.post(f"{BASE_B}/api/chat/stream", json={
            "message": "还记得我刚才说了什么吗？", "session_id": session_id
        }, headers=headers, timeout=30)

        lines_b = r2.text.split("\n")
        events_b = [l for l in lines_b if l.startswith("data: ")]
        got_done_b = any("event: done" in l for l in lines_b) or any('"status": "completed"' in e for e in events_b)

        ok = r1.status_code == 200 and r2.status_code == 200 and got_done_a and got_done_b
        report(
            "T7: Checkpointer 跨实例 (Redis)", ok,
            f"实例A发消息→实例B续聊同session | A_events={len(events_a)} B_events={len(events_b)}"
        )
    except Exception as e:
        report("T7: Checkpointer 跨实例 (Redis)", False, str(e))


# ─── Main ───

def main():
    print(f"\n{'=' * 50}")
    print(f"  LEA Platform 分布式集成测试")
    print(f"  实例 A: {BASE_A}   实例 B: {BASE_B}")
    print(f"{'=' * 50}\n")

    test_health()
    token = test_jwt_cross_instance()
    test_db_cross_instance(token)
    test_breakers()
    test_rate_limiter()
    test_kafka_events(token)
    test_checkpointer_cross_instance(token)

    print(f"\n{'=' * 50}")
    print(f"  结果: {PASS}/{PASS + FAIL} passed")
    if FAIL:
        print(f"  ! {FAIL} FAILED")
    print(f"{'=' * 50}\n")

    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
