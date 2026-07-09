"""demo_agent_flow.sh 的伴生工具 —— 模板渲染 / JSON 提取 / 审计 DB 断言。

用法（bash 侧只 shell out 到这三个子命令）：

    python _lib.py render  <tmpl_path> <output_path>          # 环境变量替换 __KEY__
    python _lib.py get     <json_string> <dot.path>            # 打印 JSON 字段值
    python _lib.py assert_audit <session_id> <expect_kinds>    # 断言 audit 表存在含指定 pii_kinds 的 user_message
    python _lib.py assert_injection <session_id>               # 断言 audit 表存在 injection_hit 行

exit code：成功 0；失败非 0（stderr 打错误信息）。
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


# ── 让 python 能 import lea_server （不依赖 pip install -e） ──────
_ROOT = Path(__file__).resolve().parents[2]
for pkg in ("lea-server", "lea-graph", "lea-apps", "lea-rag"):
    p = _ROOT / "packages" / pkg / "src"
    if p.exists():
        sys.path.insert(0, str(p))


def cmd_render(tmpl_path: str, out_path: str) -> int:
    """把 __KEY__ 占位符替换成同名环境变量的值。"""
    text = Path(tmpl_path).read_text(encoding="utf-8")

    def _sub(m: re.Match) -> str:
        key = m.group(1)
        val = os.environ.get(key)
        if val is None:
            print(f"[render] missing env var: {key}", file=sys.stderr)
            sys.exit(2)
        return val

    rendered = re.sub(r"__([A-Z][A-Z0-9_]*)__", _sub, text)
    Path(out_path).write_text(rendered, encoding="utf-8")
    return 0


def cmd_get(json_str: str, dotted: str) -> int:
    """从 JSON 字符串按 a.b.0.c 路径取值，打印到 stdout（None 打空字符串）。"""
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"[get] invalid JSON: {e}", file=sys.stderr)
        return 3
    cur = data
    for key in dotted.split("."):
        if isinstance(cur, list) and key.isdigit():
            cur = cur[int(key)]
        elif isinstance(cur, dict):
            if key not in cur:
                print(f"[get] key '{key}' not found; got keys={list(cur.keys())}", file=sys.stderr)
                return 4
            cur = cur[key]
        else:
            print(f"[get] cannot descend into {type(cur).__name__} at '{key}'", file=sys.stderr)
            return 4
    print("" if cur is None else cur)
    return 0


def _open_db():
    """开一个新的 SessionLocal —— 依赖 lea_server 已可 import。"""
    from lea_server.database import SessionLocal  # type: ignore

    return SessionLocal()


def cmd_assert_audit(session_id: str, expect_kinds: str) -> int:
    """断言 session 下存在 user_message 且 pii_kinds 命中期望的集合（逗号分隔）。

    expect_kinds='' 表示期望 pii_kinds 为空/None（Luhn 挡下学号那种）。
    """
    from lea_server.services.audit_service import AuditService  # type: ignore

    expect = {k.strip() for k in expect_kinds.split(",") if k.strip()}
    with _open_db() as db:
        rows, total = AuditService(db).query(session_id=session_id, page=1, size=50)

    user_msg_rows = [r for r in rows if r.event_type == "user_message"]
    if not user_msg_rows:
        print(f"[assert_audit] no user_message in session={session_id} (total={total})", file=sys.stderr)
        return 5

    latest = user_msg_rows[-1]
    kinds_str = (latest.pii_kinds or "").strip()
    actual = {k.strip() for k in kinds_str.split(",") if k.strip()}

    if actual != expect:
        print(
            f"[assert_audit] pii_kinds mismatch. expect={sorted(expect)} actual={sorted(actual)}",
            file=sys.stderr,
        )
        return 6

    kinds_display = ",".join(sorted(actual)) if actual else "-"
    print(f"audit#{latest.id} user_message pii_kinds={kinds_display}")
    return 0


def cmd_assert_injection(session_id: str) -> int:
    """断言 session 下存在 injection_hit 事件行。"""
    from lea_server.services.audit_service import AuditService  # type: ignore

    with _open_db() as db:
        rows, _ = AuditService(db).query(session_id=session_id, page=1, size=50)

    hits = [r for r in rows if r.event_type == "injection_hit"]
    if not hits:
        print(f"[assert_injection] no injection_hit in session={session_id}", file=sys.stderr)
        # 便于诊断，打出所有事件类型
        types = [r.event_type for r in rows]
        print(f"  seen event_types: {types}", file=sys.stderr)
        return 7

    h = hits[-1]
    print(f"audit#{h.id} injection_hit action={h.action} patterns={h.injection_patterns}")
    return 0


def cmd_assert_session_extras(session_id: str) -> int:
    """断言 session 下最新 assistant 消息的 extra JSON 至少包含一个可视化字段。

    Sprint B 的门槛：intent / skill / tool_calls / references 至少落一个到 chat_messages.extra。
    """
    from lea_server.models.chat_session import ChatMessage  # type: ignore

    with _open_db() as db:
        msgs = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == session_id, ChatMessage.role == "assistant")
            .order_by(ChatMessage.created_at)
            .all()
        )
    if not msgs:
        print(f"[assert_session_extras] no assistant messages in session={session_id}", file=sys.stderr)
        return 8

    latest = msgs[-1]
    extra = latest.extra or {}
    present = [k for k in ("intent", "skill", "tool_calls", "references") if extra.get(k)]
    if not present:
        print(
            f"[assert_session_extras] extra 全空 (msg#{latest.id})；raw extra keys={list(extra.keys())}",
            file=sys.stderr,
        )
        return 9

    print(f"session={session_id} assistant#{latest.id} extra_fields={present}")
    return 0


def cmd_assert_sandbox(session_id: str) -> int:
    """断言 session 下最新 assistant 消息的 extra.sandbox_calls 里至少一条 success 记录。

    Sprint F 门槛：数据分析类问题应触发 sandbox_exec，落库到 chat_messages.extra.sandbox_calls。
    """
    from lea_server.models.chat_session import ChatMessage  # type: ignore

    with _open_db() as db:
        msgs = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == session_id, ChatMessage.role == "assistant")
            .order_by(ChatMessage.created_at)
            .all()
        )
    if not msgs:
        print(f"[assert_sandbox] no assistant messages in session={session_id}", file=sys.stderr)
        return 10

    latest = msgs[-1]
    extra = latest.extra or {}
    sandbox_calls = extra.get("sandbox_calls") or []
    if not sandbox_calls:
        print(
            f"[assert_sandbox] extra.sandbox_calls 空 (msg#{latest.id})；LLM 没调 sandbox_exec，或 collector 没收到事件。"
            f" raw extra keys={list(extra.keys())}",
            file=sys.stderr,
        )
        return 11

    ok_calls = [c for c in sandbox_calls if c.get("status") == "success"]
    if not ok_calls:
        statuses = [c.get("status") for c in sandbox_calls]
        print(
            f"[assert_sandbox] 有 sandbox_calls={len(sandbox_calls)} 但无 success (statuses={statuses})",
            file=sys.stderr,
        )
        return 12

    latest_call = ok_calls[-1]
    print(
        f"session={session_id} assistant#{latest.id} sandbox_calls={len(sandbox_calls)}"
        f" language={latest_call.get('language')} duration_ms={latest_call.get('durationMs')}"
        f" artifacts={latest_call.get('artifactCount', 0)}"
    )
    return 0


def cmd_pack_bundle(src_dir: str, out_tarball: str) -> int:
    """确定性打包 skill 目录成 tar.gz —— 复用 SkillBundleStore.pack。

    出物：tar.gz 二进制，写到 out_tarball。stdout 打 hash + size 便于 bash grep。
    """
    from lea_graph.skills.bundle_store import SkillBundleStore  # type: ignore

    store = SkillBundleStore.from_env()
    bundle = store.pack(Path(src_dir))
    Path(out_tarball).write_bytes(bundle.tarball)
    print(f"pack sha256={bundle.sha256} size={bundle.size} files={len(bundle.manifest)}")
    return 0


def cmd_assert_sandbox_stdout(session_id: str, must_contain: str) -> int:
    """断言 session 最新 assistant 的 sandbox_calls 里有一条 stdout 含 must_contain。

    用于 SG-7 script skill demo —— 挂进沙箱的 counter.py 必须真的被执行到，
    stdout 里应能看到 COUNTER_OUTPUT= 之类的标记。
    """
    from lea_server.models.chat_session import ChatMessage  # type: ignore

    with _open_db() as db:
        msgs = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == session_id, ChatMessage.role == "assistant")
            .order_by(ChatMessage.created_at)
            .all()
        )
    if not msgs:
        print(f"[assert_sandbox_stdout] no assistant messages in session={session_id}", file=sys.stderr)
        return 20

    latest = msgs[-1]
    extra = latest.extra or {}
    calls = extra.get("sandbox_calls") or []
    if not calls:
        print(f"[assert_sandbox_stdout] no sandbox_calls in msg#{latest.id}", file=sys.stderr)
        return 21

    for call in calls:
        preview = (call.get("stdoutPreview") or "") + (call.get("stderrPreview") or "")
        if must_contain in preview:
            print(f"session={session_id} sandbox stdout contains {must_contain!r} ✓")
            return 0

    previews = [c.get("stdoutPreview", "")[:80] for c in calls]
    print(
        f"[assert_sandbox_stdout] {len(calls)} sandbox_calls but none contains {must_contain!r};"
        f" previews={previews}",
        file=sys.stderr,
    )
    return 22


COMMANDS = {
    "render": lambda a: cmd_render(a[0], a[1]),
    "get": lambda a: cmd_get(a[0], a[1]),
    "assert_audit": lambda a: cmd_assert_audit(a[0], a[1] if len(a) > 1 else ""),
    "assert_injection": lambda a: cmd_assert_injection(a[0]),
    "assert_session_extras": lambda a: cmd_assert_session_extras(a[0]),
    "assert_sandbox": lambda a: cmd_assert_sandbox(a[0]),
    "pack_bundle": lambda a: cmd_pack_bundle(a[0], a[1]),
    "assert_sandbox_stdout": lambda a: cmd_assert_sandbox_stdout(a[0], a[1]),
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__, file=sys.stderr)
        return 1
    return COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    sys.exit(main())
