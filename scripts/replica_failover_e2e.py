"""多副本故障转移验证 — 停 agent-1，确认 agent-2 从 DB 重建会话后历史可用。

判据：
  1. 第一条消息植入 marker（FAILOVER-K9X），agent-1 处理，历史写 DB。
  2. 停 agent-1，nginx 把该 session 重新哈希到 agent-2。
  3. 第二条消息追问 marker，agent-2 从 DB 重建会话（指纹自愈路径），能回答出来。
  4. 恢复 agent-1（不影响测试结果，保持栈干净）。

依赖：
  - infra/docker-compose.scale-test.yml 栈已启动（健康）
  - scripts/dev_gateway_stub.py 在 9310 端口运行（提供本机 OIDC）
  - gateway 用 docker-compose.scale-test-auth.yml 覆盖层重建（指向 stub）

用法：
  # 终端 1 — 启动 auth stub（已经在跑就跳过）
  uv run --no-sync python scripts/dev_gateway_stub.py

  # 终端 2 — 重建 gateway 使用 stub 鉴权（首次或 gateway 重建后执行一次）
  cd infra
  docker compose -f docker-compose.scale-test.yml \\
                 -f docker-compose.scale-test-auth.yml \\
                 up -d --force-recreate --no-deps gateway

  # 终端 3 — 跑测试
  uv run --no-sync python scripts/replica_failover_e2e.py
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import time

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from dev_gateway_stub import mint, _load_key  # noqa: E402

GATEWAY = "http://127.0.0.1:8080"
MARKER = "FAILOVER-K9X"

# scale-test 栈的两个 HTTP 副本容器名
AGENT_1 = "lea-agent-1"
AGENT_2 = "lea-agent-2"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {mint(_load_key())}",
        "X-Selected-Tenant-ID": "default",
        "Content-Type": "application/json",
    }


def chat(session_id: str, message: str, timeout: int = 120) -> str:
    """发送一条消息，收集完整 SSE 流，返回最终 text 内容。"""
    resp = requests.post(
        f"{GATEWAY}/api/chat/stream",
        headers=_headers(),
        json={
            "session_id": session_id,
            "agent_id": 0,
            "message": message,
            "on_disconnect": "continue",
        },
        stream=True,
        timeout=(10, timeout),
    )
    resp.raise_for_status()

    text_parts: list[str] = []
    event_name = ""
    for raw in resp.iter_lines(decode_unicode=True):
        line = raw or ""
        if line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data = line[5:].strip()
            if event_name == "text":
                try:
                    text_parts.append(json.loads(data))
                except json.JSONDecodeError:
                    text_parts.append(data)
    return "".join(text_parts)


def container_logs(name: str, tail: int = 20) -> str:
    r = subprocess.run(
        ["docker", "logs", "--tail", str(tail), name],
        capture_output=True, text=True,
    )
    return r.stdout + r.stderr


def stop_container(name: str) -> None:
    subprocess.run(["docker", "stop", name], check=True)
    print(f"  stopped {name}")


def start_container(name: str) -> None:
    subprocess.run(["docker", "start", name], check=True)
    print(f"  started {name}")


def wait_healthy(name: str, timeout: int = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Health.Status}}", name],
            capture_output=True, text=True,
        )
        status = r.stdout.strip()
        if status == "healthy":
            return
        # 没有 healthcheck 的容器 inspect 返回 "" 或 "none"，直接放行
        if status in ("", "none", "starting") and r.returncode == 0:
            time.sleep(2)
            continue
        time.sleep(2)
    raise TimeoutError(f"{name} did not become healthy within {timeout}s")


def main() -> int:
    session_id = f"failover-{int(time.time())}"
    print(f"session_id={session_id}")

    # ── 第 1 步：发植入 marker 的消息，落到 agent-1（一致性哈希）─────────────
    print("\n[1] 发植入 marker 的消息...")
    reply1 = chat(
        session_id,
        f"请把以下 marker 原样记住，稍后我会再问你：{MARKER}。记住后只需回复「已记住」。",
    )
    print(f"  reply1={reply1[:80]!r}")
    # 检查 agent-1 日志里有没有这个 session
    logs1_before = container_logs(AGENT_1)
    agent1_handled = session_id in logs1_before
    print(f"  agent-1 日志含 session_id={agent1_handled}")

    # ── 第 2 步：停 agent-1 ──────────────────────────────────────────────────
    print(f"\n[2] 停 {AGENT_1}...")
    stop_container(AGENT_1)
    # 等 nginx fail_timeout（3s），让哈希环摘掉 agent-1
    print("  等待 nginx fail_timeout 生效（5s）...")
    time.sleep(5)

    # ── 第 3 步：追问 marker，流量应落到 agent-2 ──────────────────────────────
    print("\n[3] 发追问消息（应由 agent-2 处理）...")
    try:
        reply2 = chat(session_id, "你刚才记住的 marker 是什么？原样输出。")
    except Exception as exc:
        print(f"  ERROR: {exc}")
        start_container(AGENT_1)
        return 1
    print(f"  reply2={reply2[:120]!r}")

    logs2 = container_logs(AGENT_2)
    agent2_handled = session_id in logs2
    print(f"  agent-2 日志含 session_id={agent2_handled}")

    marker_recalled = MARKER in reply2
    print(f"  marker recalled={marker_recalled}")

    # ── 第 4 步：恢复 agent-1 ────────────────────────────────────────────────
    print(f"\n[4] 恢复 {AGENT_1}...")
    start_container(AGENT_1)
    wait_healthy(AGENT_1)

    # ── 判定 ─────────────────────────────────────────────────────────────────
    passed = marker_recalled and agent2_handled
    print(f"\nPASS={str(passed).lower()}")
    if not passed:
        if not agent2_handled:
            print("  FAIL 原因: agent-2 日志里没有 session_id，流量可能没切过去")
        if not marker_recalled:
            print("  FAIL 原因: reply2 里没有 marker，历史重建失败或模型没看到")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
