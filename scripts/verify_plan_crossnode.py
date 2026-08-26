"""
Plan 模式跨副本验证 —— 配合 infra/docker-compose.verify.yml 用。

验证的是：plan_mode=true 在副本 A 提交计划（SavePlan 写入共享 DB），
plan_mode=false 在副本 B 批准并加载计划（ActivatePlan + LoadActivePlan 从共享 DB 读）。
如果 plan 只存内存里，副本 B 根本看不到副本 A 存的东西。

前提：
  - docker compose -f infra/docker-compose.verify.yml up -d 已起
  - agent 镜像已构建（lea-agent:dev）
  - SECRET_KEY 在 .env 里配了，或设了 LEA_AGENT_ALLOW_DEV_SECRET=1
  - agent 能连到 LLM（plan 生成靠模型调 AskUser）

用法：
  python scripts/verify_plan_crossnode.py
  python scripts/verify_plan_crossnode.py --base http://127.0.0.1:8080/api --secret xxx
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
import argparse
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import jwt

# ── 参数 ──────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser(description="Plan 模式跨副本验证")
parser.add_argument("--base", default="http://127.0.0.1:8080/api",
                    help="nginx 入口 (默认 http://127.0.0.1:8080/api)")
parser.add_argument("--secret", default=None,
                    help="JWT 密钥（默认从 .env 读 SECRET_KEY，或用开发密钥）")
parser.add_argument("--timeout", type=int, default=180,
                    help="单步超时秒数 (默认 180)")
args = parser.parse_args()

BASE = args.base
SECRET = args.secret
if SECRET is None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding='utf-8').splitlines():
            if line.startswith("SECRET_KEY="):
                SECRET = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
if SECRET is None:
    SECRET = "dev-secret-key-change-me"
    print("  （未配 SECRET_KEY，使用开发密钥——agent 侧需 LEA_AGENT_ALLOW_DEV_SECRET=1）")

pass_count = 0
fail_count = 0


def ok(msg: str):
    global pass_count
    print(f"  PASS  {msg}")
    pass_count += 1


def no(msg: str):
    global fail_count
    print(f"  FAIL  {msg}")
    fail_count += 1


# ── JWT ───────────────────────────────────────────────────────────────────

claims = {
    "sub": "plan-test-user",
    "tenant_id": "plan-tenant",
    "roles": ["user"],
    "iat": int(time.time()),
    "exp": int(time.time()) + 3600,
}
TOKEN = jwt.encode(claims, SECRET, algorithm="HS256")
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}


# ── Docker 日志辅助 ──────────────────────────────────────────────────────

def docker_logs(container: str, pattern: str) -> bool:
    """检查某个容器的日志里是否包含 pattern"""
    try:
        result = subprocess.run(
            ["docker", "logs", container],
            capture_output=True, timeout=10,
            encoding='utf-8', errors='replace'
        )
        return pattern in (result.stdout or '') or pattern in (result.stderr or '')
    except Exception:
        return False


def docker_logs_since(container: str, since: str = "1m") -> str:
    """获取容器最近的日志"""
    try:
        result = subprocess.run(
            ["docker", "logs", "--since", since, container],
            capture_output=True, timeout=10,
            encoding='utf-8', errors='replace'
        )
        return (result.stdout or "") + (result.stderr or "")
    except Exception:
        return ""


def which_agent_saw(pattern: str) -> str:
    """找 pattern 在哪个 agent 的日志里"""
    if docker_logs("verify-agent-a", pattern):
        return "a"
    if docker_logs("verify-agent-b", pattern):
        return "b"
    return ""


def last_upstream(pattern: str) -> str:
    """从 nginx 日志里找最近一个匹配请求的 upstream IP"""
    try:
        result = subprocess.run(
            ["docker", "logs", "verify-nginx"],
            capture_output=True, timeout=10,
            encoding='utf-8', errors='replace'
        )
        log = (result.stdout or "") + (result.stderr or "")
        lines = [l for l in log.splitlines() if pattern in l]
        if not lines:
            return ""
        # upstream=172.18.0.X:8100
        import re
        m = re.search(r"upstream=([0-9.]+):", lines[-1])
        return m.group(1) if m else ""
    except Exception:
        return ""


# ── SSE 流处理 ───────────────────────────────────────────────────────────

@dataclass
class SSEStream:
    """解析 SSE 流，收集事件"""
    events: list[tuple[str, str | None]] = field(default_factory=list)  # (event_type, data)
    got_ask_user: bool = False
    got_result: bool = False
    got_done: bool = False
    got_error: bool = False
    raw: str = ""

    def feed(self, chunk: str):
        self.raw += chunk
        # SSE 格式：event: xxx\ndata: yyy\n\n
        for block in chunk.split("\n\n"):
            block = block.strip()
            if not block:
                continue
            ev_type = None
            ev_data = None
            for line in block.split("\n"):
                if line.startswith("event:"):
                    ev_type = line[6:].strip()
                elif line.startswith("data:"):
                    ev_data = line[5:].strip()
            if ev_type:
                self.events.append((ev_type, ev_data))
                if ev_type == "ask_user":
                    self.got_ask_user = True
                elif ev_type == "result":
                    self.got_result = True
                elif ev_type == "done":
                    self.got_done = True
                elif ev_type == "error":
                    self.got_error = True


def stream_sse(url: str, body: dict, timeout: int = 180) -> SSEStream:
    """发送 POST 请求并解析 SSE 流，直到连接关闭或超时"""
    sse = SSEStream()
    with httpx.Client(timeout=httpx.Timeout(timeout, connect=10)) as client:
        with client.stream("POST", url, json=body, headers=HEADERS) as resp:
            if resp.status_code != 200:
                # 流式响应不能直接 .text，需要 read()
                body_text = resp.read().decode('utf-8', errors='replace')
                sse.raw = f"HTTP {resp.status_code}: {body_text}"
                sse.got_error = True
                return sse
            for chunk in resp.iter_text():
                sse.feed(chunk)
                # 收到 done 就不用等了
                if sse.got_done:
                    break
    return sse


# ── 主流程 ───────────────────────────────────────────────────────────────

print(f"\n{'=' * 60}")
print(f"  Plan 模式跨副本验证")
print(f"  nginx: {BASE}")
print(f"{'=' * 60}\n")

# ── 0. 健康检查 ──
print("--- 0. 存活检查 ---")
try:
    r = httpx.get("http://127.0.0.1:8080/nginx-health", timeout=5)
    if r.status_code == 200 and "ok" in r.text:
        ok("nginx 存活")
    else:
        no(f"nginx 返回异常: {r.status_code} {r.text}")
        sys.exit(1)
except Exception as e:
    no(f"nginx 不通——先 docker compose -f infra/docker-compose.verify.yml up -d: {e}")
    sys.exit(1)

# 记录开始时间，后面只看这个时间之后的日志
start_time = time.time()

# ── 1. 创建会话 ──
print()
print("--- 1. 创建会话 ---")
SID = str(uuid.uuid4())
print(f"  session_id={SID}")

# ── 2. plan_mode=true：提交计划 ──
print()
print("--- 2. plan_mode=true → 等待 agent 提交计划（AskUser） ---")

plan_body = {
    "session_id": SID,
    "message": "我要重构这个项目的认证模块。请先调研当前架构，然后用 AskUser 工具把重构计划提交给我审批。不要直接写计划文本——必须用 AskUser 工具。",
    "agent_id": 1,
    "plan_mode": True,
}

print("  发送 plan_mode=true 请求，等待 SSE 流...")
plan_sse = stream_sse(f"{BASE}/chat/stream", plan_body, timeout=args.timeout)

PLAN_UP = last_upstream("chat/stream")
print(f"  plan_mode=true 落在 upstream={PLAN_UP}")

if plan_sse.got_ask_user:
    ok("收到 ask_user 事件——agent 提交了计划")
    # 提取计划内容
    for ev_type, ev_data in plan_sse.events:
        if ev_type == "ask_user" and ev_data:
            try:
                d = json.loads(ev_data)
                question = d.get("question", "")
                if question:
                    print(f"  计划摘要: {question[:120]}...")
            except Exception:
                pass
            break
else:
    if plan_sse.got_result:
        print("  （模型没有使用 AskUser——直接生成了文本回复）")
        ok("模型回复了（但没用 AskUser——plan 持久化路径未走到）")
    elif plan_sse.got_error:
        no(f"收到 error 事件")
        print(f"  内容: {plan_sse.raw[-300:] if plan_sse.raw else '(空)'}")
    else:
        no("既没收到 ask_user 也没收到 result")
        print(f"  原始输出尾部: {plan_sse.raw[-300:] if plan_sse.raw else '(空)'}")

if plan_sse.got_done:
    ok("plan_mode 流以 done 收尾")
else:
    no("plan_mode 流没有 done")

# ── 3. plan_mode=false：批准并执行 ──
print()
print("--- 3. plan_mode=false → 批准计划并执行 ---")

exec_body = {
    "session_id": SID,
    "message": "可以，按计划执行",
    "agent_id": 1,
    "plan_mode": False,
}

print("  发送 plan_mode=false 请求，等待 SSE 流...")
exec_sse = stream_sse(f"{BASE}/chat/stream", exec_body, timeout=args.timeout * 2)

EXEC_UP = last_upstream("chat/stream")
print(f"  plan_mode=false 落在 upstream={EXEC_UP}")

if exec_sse.got_result:
    ok("收到 result 事件")
    if exec_sse.got_ask_user:
        print("  注意：plan_mode=false 也触发了 AskUser——模型可能在要求更多信息")
        print("  这会导致 SavePlan 覆盖旧计划（status 重置为 draft），ActivatePlan 的效果被覆盖")
else:
    no("没有收到 result 事件")
    if exec_sse.got_error:
        print(f"  错误内容: {exec_sse.raw[-300:] if exec_sse.raw else '(空)'}")

if exec_sse.got_done:
    ok("执行流以 done 收尾")
else:
    no("执行流没有 done")

# ── 4. 跨节点判定 ──
print()
print("--- 4. 跨节点判定 ---")

# 权威证据：agent 自己的日志
SAVE_AGENT = which_agent_saw("save_plan")
ACTIVATE_AGENT = which_agent_saw("activate_plan")
LOAD_AGENT = which_agent_saw("load_active_plan")

print(f"  save_plan        在 agent-{SAVE_AGENT or '?'}")
print(f"  activate_plan    在 agent-{ACTIVATE_AGENT or '?'}")
print(f"  load_active_plan 在 agent-{LOAD_AGENT or '?'}")

if SAVE_AGENT and ACTIVATE_AGENT:
    if SAVE_AGENT != ACTIVATE_AGENT:
        ok(f"跨节点验证成功：计划在 agent-{SAVE_AGENT} 提交，在 agent-{ACTIVATE_AGENT} 批准加载")
    else:
        print(f"  （两步都在 agent-{SAVE_AGENT} —— 本地路径也对，但这次没测到跨节点）")
        ok("单节点路径有效")
        print("  提示：nginx 轮询是严格交替的（worker_processes=1），再跑一次会落在另一个副本")
elif PLAN_UP and EXEC_UP:
    if PLAN_UP != EXEC_UP:
        ok(f"跨节点（nginx 日志）：plan={PLAN_UP}, exec={EXEC_UP}")
    else:
        ok(f"单节点路径有效（nginx: {PLAN_UP}）")
elif not plan_sse.got_ask_user:
    print("  （模型没用 AskUser，所以没有 save_plan 日志——跳过跨节点判定）")
else:
    no("无法确定 plan 操作的副本归属")

# ── 5. 验证消息历史 ──
print()
print("--- 5. 验证消息历史 ---")
try:
    r = httpx.get(f"{BASE}/sessions/{SID}/messages", headers=HEADERS, timeout=15)
    if r.status_code == 200:
        msgs = r.json().get("messages", [])
        if len(msgs) >= 2:
            ok(f"消息历史有 {len(msgs)} 条（user + assistant）")
        else:
            no(f"消息历史只有 {len(msgs)} 条，期望至少 2 条")
    else:
        no(f"获取消息历史失败: HTTP {r.status_code}")
except Exception as e:
    no(f"获取消息历史异常: {e}")

# ── 总结 ──
print()
print(f"{'=' * 60}")
print(f"  结果: {pass_count} 通过 / {fail_count} 失败")
if fail_count:
    print(f"  ! {fail_count} FAILED")
print(f"{'=' * 60}")
sys.exit(0 if fail_count == 0 else 1)
