"""跑一个真会话，堆到压缩触发线，看 session_summaries 落不落行。

    python scripts/ctx_e2e_drive.py <agent_id>

前置：scripts/ctx_e2e_seed.py 建好隔离租户，lea-config:8500 和 lea-agent:8100 在跑。

## 这个脚本验的是什么

不是 SaveSummary 的 SQL 对不对（那个已经有真库往返测试了），而是**引擎到底会不会
调到它**：

    引擎 compact 完 → 类型断言 sessionStore.(sessions.SummaryStore) → SaveSummary

这条断言在编译期只钉住了「engineStore 实现了那个接口」，钉不住「引擎拿到的那个
store 就是 engineStore」。中间隔着 engine.Config.PersistSession、opts.Backend、
OpenStore 三个开关，任何一个没接上，表现都是**一切正常、就是不落行** —— 而错误在
引擎那边是 `_ =`，不会有任何提示。

所以只能跑一遍真的看。
"""

from __future__ import annotations

import json
import pathlib
import sys
import time
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _dbconn as d

AGENT_URL = "http://127.0.0.1:8100/api/chat/stream"
SECRET = "dev-secret-12345"
TENANT = "ctxtest"
USER = "ctx-e2e-user"
THRESHOLD = 21_000

# 每条消息约 3500 个汉字 ≈ 3500 token（估算法对 CJK 是 1 token/字）。
# 六七轮就能越过 21,000。内容是无意义的重复，但**长度是真的**，而长度正是这里
# 唯一起作用的变量。
CHUNK = "上下文压缩端到端验证的填充文本。" * 220


def mint_token() -> str:
    import jwt  # PyJWT

    return jwt.encode(
        {"sub": USER, "tenant_id": TENANT, "exp": int(time.time()) + 3600},
        SECRET,
        algorithm="HS256",
    )


def send(session_id: str, agent_id: int, message: str, token: str) -> tuple[int, str]:
    import requests

    r = requests.post(
        AGENT_URL,
        json={"session_id": session_id, "agent_id": agent_id, "message": message},
        headers={"Authorization": f"Bearer {token}"},
        stream=True,
        timeout=180,
    )
    if r.status_code != 200:
        return r.status_code, r.text[:300]

    # SSE 里不是每个 data: 都是对象 —— 有裸数字（序号之类）和裸字符串，
    # 直接 .get() 会 AttributeError。这里只挑得出文本的事件，其余一律跳过。
    reply = []
    for line in r.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data:"):
            continue
        try:
            ev = json.loads(line[5:].strip())
        except Exception:
            continue
        if not isinstance(ev, dict):
            continue
        kind = ev.get("type")
        if kind in ("delta", "text", "message"):
            data = ev.get("data")
            if isinstance(data, dict):
                reply.append(str(data.get("text") or data.get("content") or ""))
            elif isinstance(data, str):
                reply.append(data)
            elif isinstance(ev.get("text"), str):
                reply.append(ev["text"])
        elif kind in ("done", "error", "end"):
            break
    return 200, "".join(reply)


def db_state(conn, session_id: str) -> tuple[int, int, int]:
    """(消息行数, 摘要行数, covered_id)。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM agent_messages WHERE tenant_id=%s AND session_id=%s",
            (TENANT, session_id),
        )
        msgs = cur.fetchone()[0]
        cur.execute(
            "SELECT covered_id FROM session_summaries WHERE tenant_id=%s AND session_id=%s",
            (TENANT, session_id),
        )
        row = cur.fetchone()
    return msgs, (1 if row else 0), (row[0] if row else 0)


def main() -> int:
    d.utf8_stdout()
    agent_id = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    session_id = f"ctxe2e-{uuid.uuid4().hex[:12]}"
    token = mint_token()
    conn = d.connect()

    print(f"会话 {session_id}  agent={agent_id}  触发线={THRESHOLD:,} token")
    print(f"每条消息约 {len(CHUNK):,} 字\n")

    try:
        for turn in range(1, 12):
            t0 = time.time()
            code, reply = send(session_id, agent_id,
                               f"[第{turn}轮] {CHUNK}\n请只回复「收到」。", token)
            msgs, has_summary, covered = db_state(conn, session_id)
            el = time.time() - t0

            if code != 200:
                print(f"第 {turn} 轮 HTTP {code}: {reply}")
                return 1

            approx = turn * len(CHUNK)
            print(f"第 {turn:2} 轮  {el:5.1f}s  累计约 {approx:>7,} token  "
                  f"消息行={msgs:<3} 摘要={'有' if has_summary else '无'}"
                  + (f" covered_id={covered}" if has_summary else "")
                  + f"  回复={reply.strip()[:24]!r}")

            if has_summary:
                print(f"\n✓ 第 {turn} 轮触发压缩，摘要已落库。")
                return verify(conn, session_id)

        print(f"\n✗ 跑满 11 轮仍未落摘要（累计约 {11*len(CHUNK):,} token，"
              f"触发线 {THRESHOLD:,}）。")
        print("  查 .local-agent.log 里有没有 summary_save_failed。")
        return 1
    finally:
        conn.close()


def verify(conn, session_id: str) -> int:
    """摘要落了之后，确认下一次 LoadHistory 真的会走摘要路径。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT covered_id, CHAR_LENGTH(summary) FROM session_summaries "
            "WHERE tenant_id=%s AND session_id=%s", (TENANT, session_id))
        covered, size = cur.fetchone()
        cur.execute(
            "SELECT MIN(id), MAX(id), COUNT(*) FROM agent_messages "
            "WHERE tenant_id=%s AND session_id=%s", (TENANT, session_id))
        lo, hi, total = cur.fetchone()
        cur.execute(
            "SELECT COUNT(*) FROM agent_messages "
            "WHERE tenant_id=%s AND session_id=%s AND id > %s",
            (TENANT, session_id, covered))
        after = cur.fetchone()[0]
        cur.execute(
            "SELECT summary FROM session_summaries WHERE tenant_id=%s AND session_id=%s",
            (TENANT, session_id))
        raw = cur.fetchone()[0]

    print(f"  covered_id={covered}  消息 id∈[{lo},{hi}] 共 {total} 条  "
          f"摘要之后剩 {after} 条  摘要 {size}B")

    ok = True
    if not (lo <= covered < hi):
        print("  ✗ covered_id 落在区间外")
        ok = False
    if after <= 0:
        print("  ✗ 摘要之后一条不剩 —— 下次 LoadHistory 会返回 [摘要 + 空]")
        ok = False

    msg = json.loads(raw)
    text = "".join(b.get("text", "") for b in (msg.get("content") or [])
                   if isinstance(b, dict))
    print(f"  摘要 role={msg.get('role')}  开头={text[:70]!r}")
    if not text.startswith("Summary of earlier conversation"):
        print("  ✗ 开头不匹配 compaction.IsSummaryMessage —— 恢复时会被当成普通 user 消息")
        ok = False

    print("\n" + ("✓ 全部通过。" if ok else "✗ 有问题，见上。"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
