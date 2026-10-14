"""真调用验证：两个 lea-tools 副本共享同一个会话的沙箱。

跑法（在 packages/lea-tools 下）：
    python verify_adopt.py

这不是单元测试 —— 它真的去 Cube 建一个沙箱、跑代码、再删掉。放在这里是因为
"跨副本认领"这件事的全部风险都在 Cube 的真实行为上（metadata 过滤支不支持、
paused 的沙箱列不列出来、envd 的 Host 头对不对），mock 一律测不出来。
"""

import asyncio
import os
import uuid

os.environ.setdefault("SANDBOX_BACKEND", "cube")
os.environ.setdefault("CUBE_API_URL", "http://10.192.241.137:3000")
os.environ.setdefault("CUBE_PROXY_HOST", "10.192.241.137")
os.environ.setdefault("CUBE_TEMPLATE_ID", "tpl-a58d55bcc1a54b5bbfbb4c00")

import httpx
from lea_tools.services._cube_sandbox import META_SESSION, find_session_sandbox
from lea_tools.services.sandbox_session import SandboxSessionManager

SESSION = f"verify-{uuid.uuid4().hex[:8]}"
TENANT = "verify-tenant"
API = os.environ["CUBE_API_URL"]

ok = True


def check(label: str, cond: bool, detail: str = ""):
    global ok
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        ok = False


async def main():
    sandbox_id = None
    try:
        # ── 副本 A：建沙箱 ──────────────────────────────────────
        a = SandboxSessionManager()
        sess = await a.create(session_id=SESSION, tenant_id=TENANT)
        sandbox_id = sess.sandbox_id
        print(f"\n副本 A 建了沙箱 {sandbox_id}  session={SESSION}\n")

        # 1. 归属标记真的写进 Cube 了吗
        rows = httpx.get(f"{API}/sandboxes",
                         params={"metadata": f"{META_SESSION}={SESSION}"},
                         timeout=10).json()
        check("Cube 上按 session_id 查得到", len(rows) == 1, f"n={len(rows)}")

        # 2. 查询函数本身
        found = await asyncio.to_thread(find_session_sandbox, SESSION)
        check("find_session_sandbox 返回同一个沙箱",
              found and found["sandbox_id"] == sandbox_id, str(found))

        # 3. A 在工作区写个文件 —— 用来验证认领之后拿到的是**同一个**工作区，
        #    而不是一个新建的空沙箱（那种情况下 sandbox_id 也可能碰巧不同，
        #    但文件内容是决定性的证据）。
        await a.execute(SESSION,
                        "open('/workspace/proof.txt','w').write('written-by-A')")

        # ── 副本 B：全新进程，dict 是空的 ───────────────────────
        b = SandboxSessionManager()
        check("副本 B 起始状态为空", len(b._sessions) == 0)

        # 这一步在改之前是 KeyError → 404
        r = await b.execute(SESSION, "print(open('/workspace/proof.txt').read())")
        check("副本 B 能直接 exec（认领成功）", r.get("exit_code") == 0, str(r)[:160])
        check("副本 B 读到的是 A 写的那个工作区",
              "written-by-A" in r.get("stdout", ""), repr(r.get("stdout"))[:120])
        check("副本 B 认到的是同一个沙箱，没有新建",
              b._sessions.get(SESSION) and b._sessions[SESSION].sandbox_id == sandbox_id)

        # 4. 最要紧的一条：全集群只有一个沙箱。认领没生效的话这里会是 2 ——
        #    而且两个都在跑，谁都不会被回收。
        rows = httpx.get(f"{API}/sandboxes",
                         params={"metadata": f"{META_SESSION}={SESSION}"},
                         timeout=10).json()
        check("同一会话只有一个沙箱", len(rows) == 1, f"n={len(rows)}")

        # 5. create() 也要能认领（副本 B 收到一个 create 请求时不该建第二个）
        sess_b = await b.create(session_id=SESSION, tenant_id=TENANT)
        check("副本 B 的 create() 复用而非新建", sess_b.sandbox_id == sandbox_id)
        rows = httpx.get(f"{API}/sandboxes",
                         params={"metadata": f"{META_SESSION}={SESSION}"},
                         timeout=10).json()
        check("create() 之后仍然只有一个沙箱", len(rows) == 1, f"n={len(rows)}")

    finally:
        if sandbox_id:
            try:
                httpx.delete(f"{API}/sandboxes/{sandbox_id}", timeout=10)
                print(f"\n已清理沙箱 {sandbox_id}")
            except Exception as e:
                print(f"\n清理失败（需要手工删）: {sandbox_id}  {e}")

    print("\n=== " + ("全部通过" if ok else "有失败项") + " ===")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
