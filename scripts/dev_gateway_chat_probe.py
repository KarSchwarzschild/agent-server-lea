"""过 gateway 走一次真聊天 —— 验证浏览器能走通的那条链，不是直连 agent。

链路：  curl → gateway:8080（OIDC 校验 + 租户解析 + 注入 X-User-ID）
              → lea-agent:8100 → 远端 lea-tools 10.192.241.137:8400 → Cube 沙箱

中文 payload 从文件读（json.dumps 走 ASCII 转义），不用命令行内联 —— 内联会被
shell 的本地代码页吃掉，症状是库里落一串 U+FFFD，看起来像后端的编码 bug。
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
GATEWAY = "http://127.0.0.1:8080"
# 带沙箱味道的提问：能不能真跑起来一次 Bash，是"聊天+配套功能接上了没"的硬判据。
PROMPT = "用 bash 在工作区建 output 目录，在里面写 output/report.txt，内容写 LEA-GW-PROBE，再 cat 确认。"


def mint() -> str:
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "dev_gateway_stub.py"), "--mint"],
        capture_output=True, text=True, cwd=ROOT,
    )
    if out.returncode != 0:
        sys.exit(f"mint failed: {out.stderr}")
    return out.stdout.strip().splitlines()[-1]


def main() -> None:
    token = mint()
    session_id = f"gw-probe-{int(time.time())}"
    payload = ROOT / ".gw_probe_payload.json"
    payload.write_text(json.dumps({
        "session_id": session_id, "message": PROMPT, "agent_id": 0,
    }), encoding="utf-8")

    print(f"session  {session_id}")
    print(f"prompt   {PROMPT}")
    print("--- stream ---")
    t0 = time.time()
    proc = subprocess.run(
        ["curl", "-sN", "--max-time", "300", "-X", "POST",
         "-H", f"Authorization: Bearer {token}",
         "-H", "X-Selected-Tenant-ID: default",
         "-H", "Content-Type: application/json",
         "--data-binary", f"@{payload}",
         f"{GATEWAY}/api/chat/stream"],
        capture_output=True,
    )
    payload.unlink(missing_ok=True)
    body = proc.stdout.decode("utf-8", errors="replace")
    # 原样落盘。Windows 控制台是 GBK，中文在屏幕上必然花 —— 判断编码只看字节，
    # 不看终端显示（踩过一次，把自己 probe 的锅当成后端 bug 查了半天）。
    raw = ROOT / ".gw_probe_stream.jsonl"
    raw.write_text(body, encoding="utf-8")
    print(f"raw      {raw}")

    # 这是 **具名 SSE**：`event:<名字>` + `data:<载荷>`，而且载荷不一定是 JSON ——
    # delta 事件的 data 就是裸文本（`data:我将使用`）。照着"每行 data 都 json.loads"
    # 去解会把全部 delta 和 tool_event 一起丢掉，得出"一个工具都没调"的结论，
    # 而那跟"模型空口胡说自己跑了工具"长得一模一样 —— 第一版就是这么误判的。
    kinds: dict[str, int] = {}
    tools: list[str] = []
    text: list[str] = []
    result: dict = {}
    event = ""
    for line in body.splitlines():
        if line.startswith("event:"):
            event = line[6:].strip()
            kinds[event] = kinds.get(event, 0) + 1
            continue
        if not line.startswith("data:"):
            continue
        data = line[5:]
        if event == "delta":
            text.append(data)
            continue
        try:
            ev = json.loads(data.strip())
        except json.JSONDecodeError:
            continue
        if event == "tool_event" and ev.get("toolName"):
            # 只记 completed，避免把同一次调用的 progress 流数成好几次工具调用。
            if ev.get("type") == "completed":
                tools.append(ev["toolName"])
        elif event == "result":
            result = ev

    reply = result.get("text") or "".join(text)
    print(f"elapsed  {time.time() - t0:.1f}s   bytes {len(body)}")
    print(f"events   {kinds}")
    print(f"tools    {tools or '(none)'}")
    print(f"result   iterations={result.get('iterations')} "
          f"in={result.get('inputTokens')} out={result.get('outputTokens')} "
          f"stopped={result.get('stopped')}")
    print(f"reply    {reply[:400]!r}")
    # anchor 本来就在提问里，模型复读一遍也能命中 —— 这条**不能**当"真跑了 bash"的
    # 证据。真判据是 tools 非空，外加下面这条：沙箱工作区里确实躺着那个文件。
    print(f"ANCHOR echoed (weak): {'LEA-GW-PROBE' in reply}")
    print(f"TOOL really ran:      {'Bash' in tools}")
    print(f"\n下一步验沙箱落盘（这才是硬判据）：\n"
          f"  python scripts/dev_gateway_get.py "
          f"\"/api/files/list?session_id={session_id}\"")
    if not kinds:
        print("raw head:", body[:600])


if __name__ == "__main__":
    main()
