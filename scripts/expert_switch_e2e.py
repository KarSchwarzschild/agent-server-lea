"""专家切换端到端验证 — PUT /sessions/:id/active-agent 全链路。

判据（按计划文档 kind-yawning-anchor.md）：

  1. 首消息带 agent_id=A → 回复含 A 的 marker；库里 active_agent_id == A。
  2. 中途 PUT 切到专家 B → 响应含 applies；下一条消息回复含 B 的 marker；
     历史完整（追问只有前几轮出现的事实，能答出来）。
  3. 取消（PUT 0）→ 回到默认 super-agent（无 A/B marker）；历史仍完整。
  6. 失效兜底：把某会话 active_agent_id 指向已删 agent → 会话仍能打开，
     不 brick（回退默认）。

前置条件：
  - lea-agent + lea-config + MySQL 已运行（docker-compose.local-services.yml 或等价栈）
  - scripts/dev_gateway_stub.py 在 9310 端口运行（提供本机 OIDC）
  - 环境变量 LEA_AGENT_URL / LEA_ACCESS_TOKEN / LEA_TENANT_ID 按需设置

用法：
  uv run --no-sync python scripts/expert_switch_e2e.py
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from _dbconn import connect, env as load_env  # noqa: E402

GATEWAY = os.environ.get("LEA_AGENT_URL", "http://127.0.0.1:8080")

# 不可伪造的 marker：分别嵌进两个测试专家的 soul_prompt，
# 要求每次回复结尾必须原样输出。
MARKER_A = "EXPERT-A-9F3"
MARKER_B = "EXPERT-B-7K1"


# ── DB helpers ────────────────────────────────────────────────────────────────

def db():
    e = load_env()
    return connect(e.get("DB_NAME", "lea_platform"))


def setup_experts(tenant_id: str) -> dict[str, int]:
    """插入两个测试专家，返回 {agent_a_id, agent_b_id}。"""
    conn = db()
    try:
        with conn.cursor() as c:
            c.execute(
                "INSERT INTO agents (name, description, soul_prompt, model, owner_scope, tenant_id) "
                "VALUES (%s,%s,%s,%s,'platform',%s)",
                (
                    "_test_expert_a",
                    "e2e test expert A",
                    f"你是测试专家A。每次回复必须在**结尾**原样输出：{MARKER_A}",
                    "qwen3-max",
                    tenant_id,
                ),
            )
            agent_a = c.lastrowid
            c.execute(
                "INSERT INTO agents (name, description, soul_prompt, model, owner_scope, tenant_id) "
                "VALUES (%s,%s,%s,%s,'platform',%s)",
                (
                    "_test_expert_b",
                    "e2e test expert B",
                    f"你是测试专家B。每次回复必须在**结尾**原样输出：{MARKER_B}",
                    "qwen3-max",
                    tenant_id,
                ),
            )
            agent_b = c.lastrowid
        conn.commit()
        return {"agent_a_id": agent_a, "agent_b_id": agent_b}
    finally:
        conn.close()


def teardown_experts(agent_ids: list[int]) -> None:
    conn = db()
    try:
        with conn.cursor() as c:
            fmt = ",".join(["%s"] * len(agent_ids))
            c.execute(f"DELETE FROM agents WHERE id IN ({fmt})", agent_ids)
        conn.commit()
    finally:
        conn.close()


def get_active_agent_id(session_id: str) -> int | None:
    """从 DB 读会话的 active_agent_id。"""
    conn = db()
    try:
        with conn.cursor() as c:
            c.execute(
                "SELECT active_agent_id FROM chat_sessions WHERE id=%s", (session_id,)
            )
            row = c.fetchone()
            return int(row[0]) if row else None
    finally:
        conn.close()


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _token() -> str:
    t = os.environ.get("LEA_ACCESS_TOKEN", "")
    if t:
        return t
    from dev_gateway_stub import mint, _load_key  # noqa: PLC0415
    return mint(_load_key())


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_token()}",
        "X-Selected-Tenant-ID": os.environ.get("LEA_TENANT_ID", "default"),
        "Content-Type": "application/json",
    }


def chat(
    session_id: str | None,
    agent_id: int,
    message: str,
    timeout: int = 120,
) -> tuple[str, str]:
    """发一条消息，返回 (reply_text, actual_session_id)。"""
    payload: dict = {
        "agent_id": agent_id,
        "message": message,
        "on_disconnect": "continue",
    }
    if session_id:
        payload["session_id"] = session_id

    resp = requests.post(
        f"{GATEWAY}/api/chat/stream",
        headers=_headers(),
        json=payload,
        stream=True,
        timeout=(10, timeout),
    )
    resp.raise_for_status()
    resp.encoding = "utf-8"

    text_parts: list[str] = []
    returned_session_id = session_id or ""
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
            if event_name in ("text", "delta"):
                text_parts.append(parsed if isinstance(parsed, str) else str(parsed))
            elif event_name == "result" and isinstance(parsed, dict) and parsed.get("text"):
                text_parts.append(parsed["text"])
            elif event_name == "session" and isinstance(parsed, dict):
                returned_session_id = parsed.get("session_id", returned_session_id)

    return "".join(text_parts), returned_session_id


def put_active_agent(session_id: str, agent_id: int) -> dict:
    """PUT /sessions/:id/active-agent，返回响应 JSON。"""
    resp = requests.put(
        f"{GATEWAY}/api/sessions/{session_id}/active-agent",
        headers=_headers(),
        json={"agent_id": agent_id},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


# ── Test cases ────────────────────────────────────────────────────────────────

def test_initial_agent(agent_a_id: int) -> tuple[bool, str]:
    """判据 1：首消息带专家A → 回复含 MARKER_A，库里记录正确。"""
    print("\n── 判据 1：首消息激活专家A ──────────────────────────────────")
    reply, session_id = chat(None, agent_a_id, "请做个简单的自我介绍。")
    print(f"  session_id={session_id}")
    print(f"  reply={reply[:120]!r}")

    marker_ok = MARKER_A in reply
    db_id = get_active_agent_id(session_id)
    db_ok = db_id == agent_a_id

    print(f"  MARKER_A in reply = {marker_ok}")
    print(f"  DB active_agent_id = {db_id}  (expected {agent_a_id})  ok={db_ok}")

    passed = marker_ok and db_ok
    print(f"  {'PASS' if passed else 'FAIL'}")
    return passed, session_id


def test_switch_agent(session_id: str, agent_a_id: int, agent_b_id: int) -> bool:
    """判据 2：中途切换到专家B → 下一条回复含 MARKER_B，历史完整。"""
    print("\n── 判据 2：中途切换到专家B ────────────────────────────────────")

    # 先植入一个只有前几轮才有的事实
    anchor = "HISTORY-ANCHOR-ZX9"
    _, _ = chat(session_id, agent_a_id, f"请记住这串字符：{anchor}。记住后只回复「已记住」。")

    # 切换
    put_resp = put_active_agent(session_id, agent_b_id)
    applies = put_resp.get("applies", "")
    print(f"  PUT response: applies={applies!r}")
    applies_ok = applies in ("next_message", "after_current_turn")

    # 给 next_message 留时间生效（after_current_turn 的情况，turn 已结束）
    time.sleep(1)

    # 下一条消息应由专家B回复
    reply, _ = chat(session_id, agent_b_id, "请做个简单的自我介绍。")
    print(f"  reply={reply[:120]!r}")
    marker_b_ok = MARKER_B in reply
    marker_a_gone = MARKER_A not in reply

    # 历史完整性：追问之前存的 anchor
    history_reply, _ = chat(session_id, agent_b_id, "我之前让你记住一串字符，是什么？原样输出。")
    print(f"  history_reply={history_reply[:120]!r}")
    history_ok = anchor in history_reply

    print(f"  applies field ok = {applies_ok}")
    print(f"  MARKER_B in reply = {marker_b_ok}")
    print(f"  MARKER_A not in reply = {marker_a_gone}")
    print(f"  history anchor recalled = {history_ok}")

    passed = applies_ok and marker_b_ok and history_ok
    print(f"  {'PASS' if passed else 'FAIL'}")
    return passed


def test_cancel_agent(session_id: str) -> bool:
    """判据 3：取消（PUT 0）→ 默认 super-agent，历史仍完整。"""
    print("\n── 判据 3：取消专家，回默认 ────────────────────────────────────")

    put_resp = put_active_agent(session_id, 0)
    applies = put_resp.get("applies", "")
    print(f"  PUT 0 response: applies={applies!r}")
    time.sleep(1)

    # 默认 super-agent 不含任何 marker
    reply, _ = chat(session_id, 0, "你好，做个简短的自我介绍。")
    print(f"  reply={reply[:120]!r}")
    no_marker_a = MARKER_A not in reply
    no_marker_b = MARKER_B not in reply

    # 历史仍可访问
    history_reply, _ = chat(session_id, 0, "这次对话里你见过什么特殊字符串？简单提一下。")
    print(f"  history_reply={history_reply[:120]!r}")
    history_ok = "HISTORY-ANCHOR-ZX9" in history_reply

    print(f"  no MARKER_A = {no_marker_a}")
    print(f"  no MARKER_B = {no_marker_b}")
    print(f"  history still accessible = {history_ok}")

    passed = no_marker_a and no_marker_b and history_ok
    print(f"  {'PASS' if passed else 'FAIL'}")
    return passed


def test_stale_agent_fallback(agent_a_id: int) -> bool:
    """判据 6：active_agent_id 指向已删 agent → 会话能打开，不 brick。"""
    print("\n── 判据 6：失效专家兜底 ────────────────────────────────────────")

    # 建一个独立会话，先用专家A写一条消息让会话落库
    _, session_id = chat(None, agent_a_id, "你好。")
    print(f"  session_id={session_id}")

    # 直接在 DB 里把 active_agent_id 指向一个不存在的 ID（999999）
    conn = db()
    try:
        with conn.cursor() as c:
            c.execute(
                "UPDATE chat_sessions SET active_agent_id=999999 WHERE id=%s",
                (session_id,),
            )
        conn.commit()
    finally:
        conn.close()
    print("  DB active_agent_id 改为 999999（不存在的 agent）")

    # 发消息，应能正常回复（回退默认，不 brick）
    try:
        reply, _ = chat(session_id, 0, "现在还能回答我吗？")
        print(f"  reply={reply[:120]!r}")
        passed = len(reply) > 0
    except Exception as exc:
        print(f"  ERROR: {exc}")
        passed = False

    print(f"  session not bricked = {passed}")
    print(f"  {'PASS' if passed else 'FAIL'}")
    return passed


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    tenant_id = os.environ.get("LEA_TENANT_ID", "default")
    results: dict[str, bool] = {}

    print("=== Setup: 创建两个测试专家 ===")
    try:
        ids = setup_experts(tenant_id)
        agent_a_id = ids["agent_a_id"]
        agent_b_id = ids["agent_b_id"]
        print(f"  agent_a_id={agent_a_id}  agent_b_id={agent_b_id}")
    except Exception as exc:
        print(f"Setup failed: {exc}")
        return 1

    session_id = ""
    try:
        passed1, session_id = test_initial_agent(agent_a_id)
        results["initial_agent"] = passed1

        if session_id:
            results["switch_agent"] = test_switch_agent(session_id, agent_a_id, agent_b_id)
            results["cancel_agent"] = test_cancel_agent(session_id)

        results["stale_fallback"] = test_stale_agent_fallback(agent_a_id)

    except Exception as exc:
        print(f"\nUnexpected error: {exc}")
        import traceback
        traceback.print_exc()

    finally:
        print("\n=== Teardown ===")
        teardown_experts([agent_a_id, agent_b_id])
        print(f"  deleted agents: {agent_a_id}, {agent_b_id}")

    print("\n\n===== 汇总 =====")
    for name, passed in results.items():
        print(f"  {'PASS' if passed else 'FAIL'}  {name}")
    overall = all(results.values())
    print(f"\nOVERALL={'pass' if overall else 'fail'}")
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
