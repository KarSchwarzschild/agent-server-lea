"""Verify that a Temporal retry resumes an agent turn from its last checkpoint.

The test submits a three-stage tool task, kills the worker after the second
tools_committed checkpoint, recreates the worker, and checks that attempt 2
continues without re-inserting attempt 1's persisted messages.
"""

from __future__ import annotations

import json
import pathlib
import queue
import subprocess
import sys
import threading
import time
from typing import Any

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from _dbconn import connect, env  # noqa: E402
from dev_gateway_stub import mint, _load_key  # noqa: E402


GATEWAY = "http://127.0.0.1:8080"
COMPOSE = [
    "docker",
    "compose",
    "-f",
    "docker-compose.yml",
    "-f",
    "docker-compose.dev.yml",
    "-f",
    "docker-compose.local-services.yml",
    "-f",
    "docker-compose.dev-auth.yml",
]


def db_connect():
    config = env()
    return connect(config.get("DB_NAME", "lea_platform"))


def fetch_checkpoint(run_id: str) -> dict[str, Any] | None:
    db = db_connect()
    try:
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT attempt, phase, iteration, checkpoint_json "
                "FROM agent_run_checkpoints WHERE run_id=%s",
                (run_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            raw = row[3]
            if isinstance(raw, str):
                raw = json.loads(raw)
            return {
                "attempt": row[0],
                "phase": row[1],
                "iteration": row[2],
                "checkpoint": raw,
            }
    finally:
        db.close()


def fetch_run(run_id: str) -> dict[str, Any] | None:
    db = db_connect()
    try:
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT status, attempt, worker_id, error_message "
                "FROM agent_runs WHERE id=%s",
                (run_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "status": row[0],
                "attempt": row[1],
                "worker_id": row[2],
                "error": row[3],
            }
    finally:
        db.close()


def fetch_evidence(run_id: str) -> dict[str, Any]:
    db = db_connect()
    try:
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT run_attempt, role, COUNT(*) FROM agent_messages "
                "WHERE run_id=%s GROUP BY run_attempt, role "
                "ORDER BY run_attempt, role",
                (run_id,),
            )
            message_counts = cursor.fetchall()
            cursor.execute(
                "SELECT run_attempt, role, message FROM agent_messages "
                "WHERE run_id=%s ORDER BY id",
                (run_id,),
            )
            messages = cursor.fetchall()
            cursor.execute(
                "SELECT run_attempt, role, COUNT(*) FROM chat_messages "
                "WHERE run_id=%s GROUP BY run_attempt, role "
                "ORDER BY run_attempt, role",
                (run_id,),
            )
            chat_counts = cursor.fetchall()
    finally:
        db.close()

    return {
        "agent_message_counts": [list(row) for row in message_counts],
        "chat_message_counts": [list(row) for row in chat_counts],
        "messages": [
            {
                "attempt": attempt,
                "role": role,
                "message": json.loads(raw) if isinstance(raw, str) else raw,
            }
            for attempt, role, raw in messages
        ],
    }


def stream_request(session_id: str, result_queue: queue.Queue[dict[str, Any]]) -> None:
    prompt = (
        "严格按两个工具阶段执行，必须等待第一阶段工具结果后才能开始第二阶段：\n"
        "第一阶段 AB：只调用一次 Bash，创建 output/temporal-recovery.txt，"
        "依次写入两行 A 和 B，然后 cat 确认。\n"
        "第二阶段 C：看到第一阶段工具结果后，再单独调用一次 Bash，"
        "执行 sleep 20，向同一文件追加一行 C，然后 cat 确认。\n"
        "最后回复文件完整内容。禁止重复执行第一阶段。"
    )
    headers = {
        "Authorization": f"Bearer {mint(_load_key())}",
        "X-Selected-Tenant-ID": "default",
        "Content-Type": "application/json",
    }
    response = requests.post(
        f"{GATEWAY}/api/chat/stream",
        headers=headers,
        json={
            "session_id": session_id,
            "agent_id": 0,
            "message": prompt,
            "on_disconnect": "continue",
        },
        stream=True,
        timeout=(10, 420),
    )
    response.raise_for_status()
    events: list[dict[str, Any]] = []
    event_name = ""
    run_id = ""
    for raw_line in response.iter_lines(decode_unicode=True):
        line = raw_line or ""
        if line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data = line[5:].strip()
            parsed: Any = data
            try:
                parsed = json.loads(data)
            except json.JSONDecodeError:
                pass
            events.append({"event": event_name, "data": parsed})
            if event_name == "session" and isinstance(parsed, dict):
                run_id = str(parsed.get("run_id", ""))
                result_queue.put({"kind": "run", "run_id": run_id})
    result_queue.put({"kind": "complete", "run_id": run_id, "events": events})


def recreate_worker() -> None:
    subprocess.run(
        COMPOSE + ["up", "-d", "--force-recreate", "--no-deps", "agent-worker"],
        cwd=ROOT / "infra",
        check=True,
    )


def wait_for_run_id(result_queue: queue.Queue[dict[str, Any]], timeout: float) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        item = result_queue.get(timeout=max(0.1, deadline - time.time()))
        if item.get("kind") == "run":
            return str(item["run_id"])
        if item.get("kind") == "error":
            raise RuntimeError(item["error"])
    raise TimeoutError("stream did not publish run_id")


def main() -> int:
    session_id = f"temporal-recovery-{int(time.time())}"
    result_queue: queue.Queue[dict[str, Any]] = queue.Queue()

    def run_stream() -> None:
        try:
            stream_request(session_id, result_queue)
        except Exception as exc:
            result_queue.put({"kind": "error", "error": repr(exc)})

    thread = threading.Thread(target=run_stream, daemon=True)
    thread.start()
    run_id = wait_for_run_id(result_queue, 30)
    print(f"session_id={session_id}")
    print(f"run_id={run_id}")

    deadline = time.time() + 180
    checkpoint_before_kill = None
    while time.time() < deadline:
        checkpoint = fetch_checkpoint(run_id)
        if (
            checkpoint
            and checkpoint["phase"] == "tools_committed"
            and checkpoint["iteration"] >= 1
        ):
            checkpoint_before_kill = checkpoint
            break
        run = fetch_run(run_id)
        if run and run["status"] in {"completed", "failed", "cancelled", "timed_out"}:
            raise RuntimeError(f"run became terminal before kill window: {run}")
        time.sleep(0.1)
    if checkpoint_before_kill is None:
        raise TimeoutError("did not observe tools_committed iteration >= 1")

    print(
        "checkpoint_before_kill="
        f"attempt:{checkpoint_before_kill['attempt']} "
        f"phase:{checkpoint_before_kill['phase']} "
        f"iteration:{checkpoint_before_kill['iteration']}"
    )
    subprocess.run(["docker", "kill", "lea-agent-worker"], check=True)
    print("worker_killed=true")
    recreate_worker()
    print("worker_recreated=true")

    deadline = time.time() + 300
    final_run = None
    while time.time() < deadline:
        final_run = fetch_run(run_id)
        if final_run and final_run["status"] in {
            "completed",
            "failed",
            "cancelled",
            "timed_out",
        }:
            break
        time.sleep(1)
    if not final_run or final_run["status"] != "completed":
        raise RuntimeError(f"run did not complete after retry: {final_run}")

    thread.join(timeout=15)
    final_checkpoint = fetch_checkpoint(run_id)
    evidence = fetch_evidence(run_id)
    print(f"final_run={json.dumps(final_run, ensure_ascii=False)}")
    print(
        "final_checkpoint="
        f"attempt:{final_checkpoint['attempt']} "
        f"phase:{final_checkpoint['phase']} "
        f"iteration:{final_checkpoint['iteration']}"
    )
    print(
        "agent_message_counts="
        + json.dumps(evidence["agent_message_counts"], ensure_ascii=False)
    )
    print(
        "chat_message_counts="
        + json.dumps(evidence["chat_message_counts"], ensure_ascii=False)
    )

    attempt_2_messages = [
        message for message in evidence["messages"] if message["attempt"] == 2
    ]
    attempt_2_roles = [message["role"] for message in attempt_2_messages]
    attempt_2_bash_commands = []
    for message in attempt_2_messages:
        for block in message["message"].get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == "Bash":
                tool_input = block.get("input") or {}
                if isinstance(tool_input, dict):
                    attempt_2_bash_commands.append(tool_input.get("command", ""))
    passed = (
        final_run["attempt"] >= 2
        and final_checkpoint is not None
        and final_checkpoint["attempt"] >= 2
        and final_checkpoint["phase"] == "turn_completed"
        and "user" not in attempt_2_roles
    )
    print(f"attempt_2_roles={attempt_2_roles}")
    print(f"attempt_2_bash_commands={attempt_2_bash_commands}")
    print(f"PASS={str(passed).lower()}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
