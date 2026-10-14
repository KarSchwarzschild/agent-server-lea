"""专家团多副本故障转移验证 — 星形（star）和接力型（handoff）两种拓扑。

两种拓扑各跑一轮，判据相同：
  1. 第一条消息让专家团调用 Bash 写一个带 marker 的文件，由 agent-1 处理。
  2. 停 agent-1，等 nginx fail_timeout（5s）生效。
  3. 第二条消息追问 marker，agent-2 从 DB 重建会话，回答里含该 marker。
  4. 接力型额外检验：SSE 流里出现过 chain_hop 事件（证明链实际运行过）。
  5. 恢复 agent-1（保持栈干净）。

前置条件：
  - infra/docker-compose.scale-test.yml 栈已健康运行
  - scripts/dev_gateway_stub.py 在 9310 端口运行
  - gateway 用 docker-compose.scale-test-auth.yml 覆盖层重建

用法：
  uv run --no-sync python scripts/team_failover_e2e.py
  uv run --no-sync python scripts/team_failover_e2e.py --topology star
  uv run --no-sync python scripts/team_failover_e2e.py --topology handoff
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time
from typing import Any

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from _dbconn import connect  # noqa: E402
from _dbconn import env as load_env

GATEWAY = os.environ.get("LEA_AGENT_URL", "http://127.0.0.1:8080")
AGENT_1 = "lea-agent-1"
AGENT_2 = "lea-agent-2"

# soul_prompt 里的 marker：每个专家在回复末尾必须原样输出。
# 接力型只检 exit 专家的 marker（它是最终回答者）。
STAR_MARKER = "STAR-WORKER-X9Z"
HANDOFF_MARKER = "HANDOFF-EXIT-Q7W"

# 让成员写到容器内的文件，内容包含 marker
STAR_FILE = "/tmp/star-failover-test.txt"
HANDOFF_FILE = "/tmp/handoff-failover-test.txt"


# ── DB helpers ──────────────────────────────────────────────────────────────

def db():
    e = load_env()
    return connect(e.get("DB_NAME", "lea_platform"))


def _bash_tool_id(conn) -> int | None:
    with conn.cursor() as c:
        c.execute("SELECT id FROM tools WHERE name='Bash' LIMIT 1")
        row = c.fetchone()
        return int(row[0]) if row else None


def setup_star_team(tenant_id: str) -> dict[str, int]:
    """创建星形专家团测试数据，返回 {leader_id, worker_id, team_id}。

    星形：团长有常驻 engine + ask_expert 工具；worker 被调用时执行 Bash。
    """
    conn = db()
    try:
        bash_id = _bash_tool_id(conn)
        with conn.cursor() as c:
            # 团长 agent
            c.execute(
                "INSERT INTO agents (name, description, soul_prompt, model, owner_scope, tenant_id) "
                "VALUES (%s,%s,%s,%s,'platform',%s)",
                (
                    "_test_star_leader",
                    "e2e test star team leader",
                    "你是星形团的团长。收到任务后调用 ask_expert 让 worker 处理，并汇总结果。",
                    "qwen3-max",
                    tenant_id,
                ),
            )
            leader_id = c.lastrowid
            # worker agent（soul_prompt 嵌入不可伪造 marker）
            c.execute(
                "INSERT INTO agents (name, description, soul_prompt, model, owner_scope, tenant_id) "
                "VALUES (%s,%s,%s,%s,'platform',%s)",
                (
                    "_test_star_worker",
                    "e2e test star team worker",
                    f"你是星形团的工人专家。每次回复必须在**结尾**原样输出：{STAR_MARKER}",
                    "qwen3-max",
                    tenant_id,
                ),
            )
            worker_id = c.lastrowid
            # 给 worker 绑 Bash 工具
            if bash_id:
                c.execute(
                    "INSERT INTO agent_tool_bindings (agent_id, tool_id, enabled) VALUES (%s,%s,1) "
                    "ON DUPLICATE KEY UPDATE enabled=1",
                    (worker_id, bash_id),
                )
            # agent_teams 行（strategy="" → 星型）
            c.execute(
                "INSERT INTO agent_teams (agent_id, strategy, decomposer_prompt, synthesizer_prompt) "
                "VALUES (%s,'','','')",
                (leader_id,),
            )
            team_id = c.lastrowid
            # 成员：worker 角色
            c.execute(
                "INSERT INTO agent_team_members (team_id, member_agent_id, role, name) "
                "VALUES (%s,%s,'worker','worker')",
                (team_id, worker_id),
            )
        conn.commit()
        return {"leader_id": leader_id, "worker_id": worker_id, "team_id": team_id}
    finally:
        conn.close()


def setup_handoff_team(tenant_id: str) -> dict[str, int]:
    """创建接力型专家团测试数据，返回 {leader_id, worker_id, exit_id, team_id}。

    handoff 链：entry → worker → exit。
    ValidateChain 要求 roster 里至少有一个 worker 角色，只有 entry+exit 会被拒绝。
    """
    conn = db()
    try:
        bash_id = _bash_tool_id(conn)
        with conn.cursor() as c:
            # entry 专家（团长，handoff 模式下 engine=nil，只负责接收任务）
            c.execute(
                "INSERT INTO agents (name, description, soul_prompt, model, owner_scope, tenant_id) "
                "VALUES (%s,%s,%s,%s,'platform',%s)",
                (
                    "_test_handoff_leader",
                    "e2e test handoff team leader",
                    "你是接力团的入口专家。收到任务后简要复述任务内容，然后把任务交给 worker 专家。",
                    "qwen3-max",
                    tenant_id,
                ),
            )
            leader_id = c.lastrowid
            if bash_id:
                c.execute(
                    "INSERT INTO agent_tool_bindings (agent_id, tool_id, enabled) VALUES (%s,%s,1) "
                    "ON DUPLICATE KEY UPDATE enabled=1",
                    (leader_id, bash_id),
                )
            # worker 专家（ValidateChain 要求至少一个 worker 角色）
            c.execute(
                "INSERT INTO agents (name, description, soul_prompt, model, owner_scope, tenant_id) "
                "VALUES (%s,%s,%s,%s,'platform',%s)",
                (
                    "_test_handoff_worker",
                    "e2e test handoff worker expert",
                    "你是接力团的执行专家。负责处理任务主体，完成后把结果交给 exit 专家。",
                    "qwen3-max",
                    tenant_id,
                ),
            )
            worker_id = c.lastrowid
            # exit 专家（产出最终答案，soul_prompt 带 marker）
            c.execute(
                "INSERT INTO agents (name, description, soul_prompt, model, owner_scope, tenant_id) "
                "VALUES (%s,%s,%s,%s,'platform',%s)",
                (
                    "_test_handoff_exit",
                    "e2e test handoff exit expert",
                    f"你是接力团的验收专家。负责汇总结果，回复必须在**结尾**原样输出：{HANDOFF_MARKER}",
                    "qwen3-max",
                    tenant_id,
                ),
            )
            exit_id = c.lastrowid
            # agent_teams 行（strategy="handoff"）
            c.execute(
                "INSERT INTO agent_teams (agent_id, strategy, decomposer_prompt, synthesizer_prompt) "
                "VALUES (%s,'handoff','','')",
                (leader_id,),
            )
            team_id = c.lastrowid
            # entry → worker → exit
            c.execute(
                "INSERT INTO agent_team_members (team_id, member_agent_id, role, name) "
                "VALUES (%s,%s,'entry','entry')",
                (team_id, leader_id),
            )
            c.execute(
                "INSERT INTO agent_team_members (team_id, member_agent_id, role, name) "
                "VALUES (%s,%s,'worker','worker')",
                (team_id, worker_id),
            )
            c.execute(
                "INSERT INTO agent_team_members (team_id, member_agent_id, role, name) "
                "VALUES (%s,%s,'exit','exit')",
                (team_id, exit_id),
            )
        conn.commit()
        return {
            "leader_id": leader_id,
            "worker_id": worker_id,
            "exit_id": exit_id,
            "team_id": team_id,
        }
    finally:
        conn.close()


def teardown_agents(agent_ids: list[int]) -> None:
    conn = db()
    try:
        with conn.cursor() as c:
            fmt = ",".join(["%s"] * len(agent_ids))
            # agent_team_members.member_agent_id FK 没有 ON DELETE CASCADE，
            # 必须先删 agent_teams（它会 cascade 删 agent_team_members），再删 agents。
            c.execute(f"DELETE FROM agent_teams WHERE agent_id IN ({fmt})", agent_ids)
            c.execute(f"DELETE FROM agents WHERE id IN ({fmt})", agent_ids)
        conn.commit()
    finally:
        conn.close()


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def _token() -> str:
    t = os.environ.get("LEA_ACCESS_TOKEN", "")
    if t:
        return t
    from dev_gateway_stub import _load_key, mint  # noqa: PLC0415
    return mint(_load_key())


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_token()}",
        "X-Selected-Tenant-ID": os.environ.get("LEA_TENANT_ID", "default"),
        "Content-Type": "application/json",
    }


def chat(
    session_id: str, agent_id: int, message: str, timeout: int = 180
) -> tuple[str, list[dict[str, Any]]]:
    """发一条消息，返回 (assistant_text, sse_events)。"""
    resp = requests.post(
        f"{GATEWAY}/api/chat/stream",
        headers=_headers(),
        json={
            "session_id": session_id,
            "agent_id": agent_id,
            "message": message,
            "on_disconnect": "continue",
        },
        stream=True,
        timeout=(10, timeout),
    )
    resp.raise_for_status()
    resp.encoding = "utf-8"

    events: list[dict[str, Any]] = []
    text_parts: list[str] = []
    event_name = ""
    for raw in resp.iter_lines(decode_unicode=True):
        line = raw or ""
        if line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data = line[5:].strip()
            try:
                parsed = json.loads(data)
            except json.JSONDecodeError:
                parsed = data
            events.append({"event": event_name, "data": parsed})
            if event_name in ("text", "delta"):
                text_parts.append(parsed if isinstance(parsed, str) else str(parsed))
            elif event_name == "result" and isinstance(parsed, dict) and parsed.get("text"):
                # handoff chain: intermediate deltas come via tool_event; final answer is in result.text
                text_parts.append(parsed["text"])
    return "".join(text_parts), events


# ── Container helpers ────────────────────────────────────────────────────────

def container_logs(name: str, tail: int = 30) -> str:
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
        if status in ("", "none") and r.returncode == 0:
            return
        time.sleep(2)
    raise TimeoutError(f"{name} did not become healthy within {timeout}s")


# ── Core test ────────────────────────────────────────────────────────────────

def run_failover(
    topology: str,
    leader_id: int,
    marker: str,
    sandbox_file: str,
    extra_checks: list[str] | None = None,
) -> bool:
    """一次故障转移测试。返回 True=PASS。"""
    session_id = f"team-failover-{topology}-{int(time.time())}"
    print(f"\n{'─'*60}")
    print(f"topology={topology}  session_id={session_id}  leader_id={leader_id}")

    # ── Step 1 ───────────────────────────────────────────────────────────────
    msg1 = (
        f"请你（或让你的成员）用 Bash 工具把字符串 {marker} 写入文件 {sandbox_file}，"
        f"并把这个 marker 原样输出在你的回复里。"
    )
    print("\n[1] 发第一条消息（含 Bash 工具调用请求）...")
    try:
        reply1, events1 = chat(session_id, leader_id, msg1)
    except Exception as exc:
        print(f"  ERROR step1: {exc}")
        return False

    print(f"  reply1={reply1[:100]!r}")
    tool_calls1 = [e for e in events1 if e["event"] == "tool_call"]
    chain_hops1 = [e for e in events1 if e["event"] == "chain_hop"]
    bash_called = any(
        isinstance(e.get("data"), dict) and e["data"].get("name") == "Bash"
        for e in tool_calls1
    )
    print(f"  bash_called={bash_called}  chain_hops={len(chain_hops1)}")

    logs1 = container_logs(AGENT_1)
    agent1_handled = session_id in logs1
    print(f"  agent-1 handled={agent1_handled}")

    # ── Step 2 ───────────────────────────────────────────────────────────────
    print(f"\n[2] 停 {AGENT_1}...")
    stop_container(AGENT_1)
    print("  等待 nginx fail_timeout（5s）...")
    time.sleep(5)

    # ── Step 3 ───────────────────────────────────────────────────────────────
    msg2 = "你刚才的回复里有一个 marker，请原样告诉我那个 marker 是什么。"
    print("\n[3] 发追问消息（应由 agent-2 处理）...")
    try:
        reply2, events2 = chat(session_id, leader_id, msg2)
    except Exception as exc:
        print(f"  ERROR step3: {exc}")
        start_container(AGENT_1)
        return False

    print(f"  reply2={reply2[:120]!r}")
    chain_hops2 = [e for e in events2 if e["event"] == "chain_hop"]

    logs2 = container_logs(AGENT_2)
    agent2_handled = session_id in logs2
    marker_recalled = marker in reply2
    print(f"  agent-2 handled={agent2_handled}")
    print(f"  marker recalled={marker_recalled}")
    if topology == "handoff":
        print(f"  chain_hops turn2={len(chain_hops2)}")

    # ── Step 4 ───────────────────────────────────────────────────────────────
    print(f"\n[4] 恢复 {AGENT_1}...")
    start_container(AGENT_1)
    wait_healthy(AGENT_1)

    # ── Verdict ──────────────────────────────────────────────────────────────
    checks: dict[str, bool] = {
        "agent1_handled": agent1_handled,
        "agent2_handled": agent2_handled,
        "marker_recalled": marker_recalled,
    }
    if topology == "handoff":
        # 至少有一轮（turn1 或 turn2）出现过 chain_hop，证明链实际运行
        checks["chain_hop_seen"] = len(chain_hops1) > 0 or len(chain_hops2) > 0

    passed = all(checks.values())
    for k, v in checks.items():
        status = "PASS" if v else "FAIL"
        print(f"  {status} {k}={v}")
    print(f"\n  {'PASS' if passed else 'FAIL'}")
    return passed


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="专家团多副本故障转移 e2e 测试")
    parser.add_argument(
        "--topology",
        choices=["star", "handoff", "both"],
        default="both",
        help="要测的拓扑（默认 both）",
    )
    args = parser.parse_args()

    tenant_id = "default"
    results: dict[str, bool] = {}

    run_star = args.topology in ("star", "both")
    run_handoff = args.topology in ("handoff", "both")

    star_ids: dict[str, int] = {}
    handoff_ids: dict[str, int] = {}

    # ── Setup ─────────────────────────────────────────────────────────────────
    try:
        if run_star:
            print("=== Setup: 星形专家团 ===")
            star_ids = setup_star_team(tenant_id)
            print(f"  leader_id={star_ids['leader_id']}  worker_id={star_ids['worker_id']}")

        if run_handoff:
            print("=== Setup: 接力型专家团 ===")
            handoff_ids = setup_handoff_team(tenant_id)
            print(f"  leader_id={handoff_ids['leader_id']}  exit_id={handoff_ids['exit_id']}")
    except Exception as exc:
        print(f"Setup failed: {exc}")
        return 1

    # ── Tests ──────────────────────────────────────────────────────────────────
    try:
        if run_star:
            print("\n\n===== 测试：星形专家团故障转移 =====")
            results["star"] = run_failover(
                topology="star",
                leader_id=star_ids["leader_id"],
                marker=STAR_MARKER,
                sandbox_file=STAR_FILE,
            )

        if run_handoff:
            print("\n\n===== 测试：接力型（handoff）专家团故障转移 =====")
            results["handoff"] = run_failover(
                topology="handoff",
                leader_id=handoff_ids["leader_id"],
                marker=HANDOFF_MARKER,
                sandbox_file=HANDOFF_FILE,
            )

    finally:
        # ── Teardown ───────────────────────────────────────────────────────────
        print("\n=== Teardown ===")
        all_ids: list[int] = []
        if star_ids:
            all_ids += [star_ids["leader_id"], star_ids["worker_id"]]
        if handoff_ids:
            all_ids += [handoff_ids["leader_id"], handoff_ids["worker_id"], handoff_ids["exit_id"]]
        if all_ids:
            teardown_agents(all_ids)
            print(f"  deleted agent ids: {all_ids}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n\n===== 汇总 =====")
    for topo, passed in results.items():
        print(f"  {'PASS' if passed else 'FAIL'}  {topo}")
    overall = all(results.values())
    print(f"\nOVERALL={'pass' if overall else 'fail'}")
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
