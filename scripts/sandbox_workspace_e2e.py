"""验收：沙箱执行和会话工作区，在**前端拿得到的那一层**上到底有没有数据。

    LEA_AGENT_URL=http://127.0.0.1:8080 python scripts/sandbox_workspace_e2e.py

前置：infra/docker-compose.verify.yml 起着（nginx :8080 轮询 agent-a/agent-b），
且 lea-agent 的 LEA_TOOLS_URL 指向真的 lea-tools（沙箱后端不能是 mock）。

## 为什么要有这条脚本

frontend-lab 刚把沙箱卡片和工作区文件树从"一坨 pretty(JSON)"改成结构化渲染：
代码 / stdout / stderr / 产物 各自成块，文件按目录分组。这些渲染**全部依赖后端
真的把那些字段填上**，而前端那侧无论有没有数据都渲染得出来 —— 空字段就是不显示，
看起来一切正常。所以"改完了"不能靠看界面确认，只能靠在真链路上断言字段非空。

前端有两条渲染路径，各读一种形状，必须分别验：

    直播  SSE tool_event 的 data（agentloop 的 tool.Event）  → sandboxFromEvent()
    回放  chat_messages.extra.sandbox_calls[i]（Go collector 归纳） → sandboxFromCall()

两条都验，是因为它们在界面上必须长得一样 —— 只要不一样，用户会以为刷新把东西
弄丢了。而它们来自**两套代码**（一个是引擎直出，一个是 collector 二次归纳），
只测一条完全可能另一条是空的。

## 六条判据

    ① 直播：Bash/code_interpreter 的 tool_event 带 input（代码在里面）
    ② 直播：完成事件带 output，且行上有 stream 标签（stdout/stderr 分得开）
    ③ 落库：extra.sandbox_calls[i] 有 codePreview + status + stdoutPreview
    ④ 工作区：/api/files/list 返回**带目录的相对路径**（不是一串裸文件名）
    ⑤ 工作区：show_hidden=true 真的改变结果集（dist/ 那类目录默认被剪掉）
    ⑥ 产物：artifacts 有没有数据 —— **如实报告，不预设通过**

## ⚠️ ⑥ 是"观察"不是"断言"

memory 里记着：cube 那条链的 artifacts 一直是 []，上游没供数。前端已经把产物
渲染成可点的 chip 了，但如果这里一直是空，那个 UI 就是死代码。所以这一条只报告
实况，不判定成失败 —— 把"没数据"和"坏了"分开，否则每次跑都红一条，久了没人看。

## ⚠️ 判据落在锚点上，不落在"回答对不对"

stdout 里要带一个模型编不出来的字符串（ANCHOR）。只断言"stdoutPreview 非空"不够：
模型可能根本没跑代码，而是自己写了一段"输出应该是……"。锚点由脚本生成、只在
被真正执行的命令里出现。
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _dbconn as d

AGENT_URL = os.getenv("LEA_AGENT_URL", "http://127.0.0.1:8080").rstrip("/")
SECRET = d.env()["SECRET_KEY"]
TENANT = "default"
USER = "sandbox-ws-e2e"
AGENT_ID = int(os.getenv("LEA_AGENT_ID", "2"))

ANCHOR = f"SBX-{uuid.uuid4().hex[:8].upper()}"

# 这条指令刻意要求三件事，各自对应一条判据：
#   report/summary.txt  → ④ 路径里必须有目录，否则验不出分组
#   dist/bundle.txt     → ⑤ dist 是 lea-tools 默认剪掉的目录之一
#   cat + echo ANCHOR   → ①②③ stdout 里得有个编不出来的串
TASK = (
    f"请用 Bash 在当前工作目录下做这几件事，一次跑完：\n"
    f"1. mkdir -p report dist\n"
    f"2. 把 {ANCHOR} 写进 report/summary.txt\n"
    f"3. 把 {ANCHOR} 写进 dist/bundle.txt\n"
    f"4. cat report/summary.txt\n"
    f"做完把 cat 的输出原样告诉我。"
)


# ── HTTP ──────────────────────────────────────────────────────────────────

def mint(user: str) -> str:
    import jwt
    return jwt.encode(
        {"sub": user, "tenant_id": TENANT, "exp": int(time.time()) + 3600},
        SECRET, algorithm="HS256",
    )


def hdr(user: str) -> dict:
    return {"Authorization": f"Bearer {mint(user)}"}


def turn_running(session_id: str) -> bool:
    import requests
    try:
        r = requests.get(f"{AGENT_URL}/api/chat/status/{session_id}",
                         headers=hdr(USER), timeout=10)
        return r.status_code == 200 and bool(r.json().get("running"))
    except requests.RequestException:
        return False


def ask_capturing(session_id: str, message: str) -> tuple[int, list[dict], str]:
    """发一条消息，把 **tool_event 的原始 data** 全留下来。

    前端 sandboxFromEvent 吃的就是这些 data 对象，所以判据 ①② 必须落在这里 ——
    从库里反推不行，落库那份是 collector 归纳过的，正好是判据 ③ 要单独验的那份。
    """
    import requests
    events: list[dict] = []
    text_parts: list[str] = []
    r = requests.post(
        f"{AGENT_URL}/api/chat/stream",
        json={"session_id": session_id, "agent_id": AGENT_ID, "message": message},
        headers=hdr(USER), stream=True, timeout=300,
    )
    if r.status_code != 200:
        print(f"    HTTP {r.status_code}: {r.text[:300]}")
        return r.status_code, events, ""

    event_name = ""
    for line in r.iter_lines(decode_unicode=True):
        if line is None:
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip()
            continue
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        # delta 的 Data 是**裸字符串**，不是对象（agent_session.go:366），
        # 所以 payload 可能不是 dict —— 先分流再取字段。
        if not isinstance(payload, dict):
            if event_name == "delta":
                text_parts.append(str(payload))
            continue
        kind = event_name or str(payload.get("type") or "")
        if kind == "tool_event":
            # SSE 的 data 段**就是** tool.Event 本身（router/stream.go:194 直接把
            # ev.Data 当 Data 写出去）。别去取 payload["data"] —— tool.Event 自己
            # 也有一个 data 字段（progress 事件的产物/输出装在那儿），取错一层
            # 会把 Started/Completed 事件整个丢掉。
            events.append(payload)
        elif kind == "delta":
            text_parts.append(str(payload.get("content") or payload.get("delta") or ""))

    for _ in range(120):
        if not turn_running(session_id):
            break
        time.sleep(1)
    return 200, events, "".join(text_parts)


def new_session() -> str:
    import requests
    r = requests.post(f"{AGENT_URL}/api/sessions",
                      json={"title": "sandbox-ws-e2e"}, headers=hdr(USER), timeout=30)
    r.raise_for_status()
    body = r.json()
    return str(body.get("session_id") or body.get("id"))


def list_files(session_id: str, show_hidden: bool) -> dict:
    import requests
    url = f"{AGENT_URL}/api/files/list?session_id={session_id}"
    if show_hidden:
        url += "&show_hidden=true"
    r = requests.get(url, headers=hdr(USER), timeout=60)
    if r.status_code != 200:
        return {"_status": r.status_code, "_body": r.text[:300]}
    return r.json()


# ── 库 ────────────────────────────────────────────────────────────────────

def sandbox_calls(conn, session_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT extra FROM chat_messages WHERE session_id=%s AND role='assistant' "
            "ORDER BY id",
            (session_id,),
        )
        out: list[dict] = []
        for (extra,) in cur.fetchall():
            if not extra:
                continue
            blob = json.loads(extra) if isinstance(extra, (str, bytes)) else extra
            out.extend(blob.get("sandbox_calls") or [])
        return out


def cleanup(conn, session_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM session_attachments WHERE session_id=%s", (session_id,))
        cur.execute("DELETE FROM agent_messages WHERE session_id=%s", (session_id,))
        cur.execute("DELETE FROM chat_messages WHERE session_id=%s", (session_id,))
        cur.execute("DELETE FROM chat_sessions WHERE id=%s", (session_id,))
    conn.commit()


# ── 主流程 ────────────────────────────────────────────────────────────────

def step(n, title): print(f"\n[{n}] {title}")


def main() -> int:
    conn = d.connect()
    session_id = new_session()
    print(f"session={session_id} anchor={ANCHOR}")
    verdicts: list[tuple[str, bool | None, str]] = []

    try:
        step(0, "发一条会进沙箱的消息")
        status, events, answer = ask_capturing(session_id, TASK)
        if status != 200:
            print(f"  ✗ /api/chat/stream 返回 {status}，后面全部无从谈起")
            return 1
        print(f"  收到 {len(events)} 条 tool_event；回复 {len(answer)} 字")

        sandbox_events = [
            e for e in events
            if str(e.get("toolName")) in ("Bash", "code_interpreter")
        ]
        print(f"  其中沙箱类 {len(sandbox_events)} 条："
              f"{[e.get('type') for e in sandbox_events]}")

        step(1, "直播：沙箱事件带 input（代码）")
        with_input = [e for e in sandbox_events if e.get("input")]
        ok = bool(with_input)
        if ok:
            sample = json.dumps(with_input[0]["input"], ensure_ascii=False)[:160]
            print(f"  ✓ {len(with_input)} 条带 input，例：{sample}")
        else:
            print("  ✗ 没有任何沙箱事件带 input —— 前端卡片的代码块会永远是空的")
        verdicts.append(("① 直播 input.code", ok, ""))

        step(2, "直播：完成事件带 output，且行上有 stream 标签")
        lines: list[dict] = []
        for e in sandbox_events:
            for ln in e.get("output") or []:
                if isinstance(ln, dict):
                    lines.append(ln)
        streams = {str(ln.get("stream")) for ln in lines}
        has_anchor = any(ANCHOR in str(ln.get("text") or "") for ln in lines)
        ok = bool(lines) and streams.issubset({"stdout", "stderr"}) and bool(streams)
        print(f"  {len(lines)} 行输出，stream 取值={sorted(streams)}，"
              f"锚点在直播输出里={'是' if has_anchor else '否'}")
        if not ok:
            print("  ✗ 前端靠 stream 字段把 stderr 标红，没有它 traceback 会跟正常输出同色")
        verdicts.append(("② 直播 output.stream", ok, ""))

        step(3, "落库：extra.sandbox_calls 的字段")
        calls = sandbox_calls(conn, session_id)
        print(f"  sandbox_calls 共 {len(calls)} 条")
        for i, c in enumerate(calls):
            print(f"    [{i}] status={c.get('status')!r} language={c.get('language')!r} "
                  f"code={len(str(c.get('codePreview') or ''))}B "
                  f"stdout={len(str(c.get('stdoutPreview') or ''))}B "
                  f"stderr={len(str(c.get('stderrPreview') or ''))}B "
                  f"artifacts={len(c.get('artifacts') or [])} "
                  f"durationMs={c.get('durationMs')}")
        has_code = any(c.get("codePreview") for c in calls)
        has_status = any(c.get("status") for c in calls)
        anchor_in_db = any(ANCHOR in str(c.get("stdoutPreview") or "") for c in calls)
        ok = bool(calls) and has_code and has_status
        if not anchor_in_db:
            print("  ⚠ 锚点不在 stdoutPreview 里 —— 代码可能没真跑，或输出没被收上来")
        verdicts.append(("③ 落库 sandbox_calls", ok and anchor_in_db, ""))

        step(4, "工作区：路径带目录")
        ws = list_files(session_id, show_hidden=False)
        if "_status" in ws:
            print(f"  ✗ /api/files/list HTTP {ws['_status']}: {ws['_body']}")
            verdicts.append(("④ 工作区目录结构", False, ""))
            verdicts.append(("⑤ show_hidden", False, ""))
        else:
            files = ws.get("files") or []
            print(f"  store={ws.get('store')!r} truncated={ws.get('truncated')!r} "
                  f"files={len(files)}")
            for f in files[:12]:
                print(f"    {f.get('path')}  {f.get('size')}B in_output={f.get('in_output')}")
            nested = [f for f in files if "/" in str(f.get("path") or "")]
            ok = bool(nested)
            if ok:
                print(f"  ✓ {len(nested)} 个路径带目录 —— 前端的分组有东西可分")
            else:
                print("  ✗ 全是裸文件名，验不出目录分组"
                      "（可能模型没建子目录，也可能后端把路径拍平了）")
            verdicts.append(("④ 工作区目录结构", ok, ""))

            step(5, "工作区：show_hidden 改变结果集")
            ws_h = list_files(session_id, show_hidden=True)
            hidden_files = ws_h.get("files") or []
            shown = {str(f.get("path")) for f in files}
            all_paths = {str(f.get("path")) for f in hidden_files}
            extra = sorted(all_paths - shown)
            print(f"  默认 {len(shown)} 个 → show_hidden {len(all_paths)} 个，"
                  f"多出来 {len(extra)}：{extra[:8]}")
            ok = bool(extra)
            if not ok:
                print("  ⚠ 没多出文件。要么模型没往 dist/ 写，要么 _HIDDEN_DIRS 没生效")
            verdicts.append(("⑤ show_hidden", ok, ""))

        step(6, "产物 artifacts（只观察，不判定）")
        ev_artifacts = []
        for e in sandbox_events:
            data = e.get("data") or {}
            if isinstance(data, dict):
                ev_artifacts.extend(data.get("artifacts") or [])
        db_artifacts = [a for c in calls for a in (c.get("artifacts") or [])]
        print(f"  直播事件里的 artifacts：{len(ev_artifacts)} 条 {ev_artifacts[:3]}")
        print(f"  落库 sandbox_calls 里的 artifacts：{len(db_artifacts)} 条 {db_artifacts[:3]}")
        if not ev_artifacts and not db_artifacts:
            print("  → 上游没供数。前端的产物 chip 目前是**没有数据可渲染的**，"
                  "这跟坏了不是一回事，但也别当成已接通。")
        verdicts.append(("⑥ artifacts 有数据", None, "观察项"))

        print("\n" + "=" * 64)
        failed = 0
        for name, ok, note in verdicts:
            if ok is None:
                mark = "○"
            elif ok:
                mark = "✓"
            else:
                mark = "✗"
                failed += 1
            print(f"  {mark} {name}{('  (' + note + ')') if note else ''}")
        print("=" * 64)
        return 1 if failed else 0
    finally:
        if os.getenv("KEEP_SESSION") != "1":
            cleanup(conn, session_id)
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
