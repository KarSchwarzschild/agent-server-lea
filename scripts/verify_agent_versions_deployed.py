"""部署后验证 agent 配置版本化：建→改→回滚，全程看版本链。

绕开网关直连 lea-config（容器内 127.0.0.1:8500），用 internal token + 受信头。
网关那条路已经单独验过（它只是把 JWT 换成这三个头），这里要验的是版本逻辑本身。

跑完删掉自己建的 agent，不留垃圾。

跑法：
    docker cp scripts/verify_agent_versions_deployed.py lea-config-test:/tmp/v.py
    docker exec lea-config-test python /tmp/v.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8500"
TENANT = os.getenv("LEA_TEST_TENANT", "test2")
USER = "verify-versions-bot"


def call(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            # 受信头只在带对的 internal token 时才被接受（lea-authkit）。
            "X-Internal-Token": os.getenv("LEA_INTERNAL_TOKEN", ""),
            "X-User-ID": USER,
            "X-Tenant-ID": TENANT,
            "X-User-Role": "admin",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def revs(aid: int) -> list[dict]:
    _, d = call("GET", f"/agents/{aid}/versions")
    return d["items"] if isinstance(d, dict) else []


def main() -> int:
    ok = True
    checks: list[tuple[str, bool]] = []

    print("=== 建一个 agent ===")
    st, agent = call(
        "POST",
        "/agents",
        {"name": f"版本验证_{os.getpid()}", "soul_prompt": "原始人格", "model": "glm-4-air"},
    )
    if st != 201:
        print(f"创建失败 {st}: {agent}")
        return 1
    aid = agent["id"]
    print(f"  id={aid}")

    try:
        v = revs(aid)
        print(f"  版本链: {[(x['revision'], x['change_kind']) for x in v]}")
        checks.append(("创建就有 revision 1 (create)",
                       len(v) == 1 and v[0]["revision"] == 1
                       and v[0]["change_kind"] == "create"))

        print("\n=== 改两次 ===")
        call("PUT", f"/agents/{aid}", {"soul_prompt": "第二版"})
        call("PUT", f"/agents/{aid}", {"soul_prompt": "第三版", "max_turns": 50})
        v = revs(aid)
        print(f"  版本链: {[(x['revision'], x['change_kind']) for x in v]}")
        print(f"  最新 note: {v[0]['change_note']}")
        checks.append(("两次编辑各追加一条", [x["revision"] for x in v] == [3, 2, 1]))
        checks.append(("note 列出改了哪些字段",
                       "soul_prompt" in v[0]["change_note"]
                       and "max_turns" in v[0]["change_note"]))

        print("\n=== 空保存不该留版本 ===")
        call("PUT", f"/agents/{aid}", {})
        checks.append(("空保存不留版本", len(revs(aid)) == 3))

        print("\n=== 看 revision 1 的快照 ===")
        _, v1 = call("GET", f"/agents/{aid}/versions/1")
        print(f"  soul_prompt = {v1['snapshot']['soul_prompt']!r}")
        checks.append(("历史快照内容正确", v1["snapshot"]["soul_prompt"] == "原始人格"))

        print("\n=== 回滚到 revision 1 ===")
        st, _ = call("POST", f"/agents/{aid}/rollback", {"revision": 1})
        v = revs(aid)
        print(f"  版本链: {[(x['revision'], x['change_kind']) for x in v]}")
        _, cur = call("GET", f"/agents/{aid}/versions/4")
        print(f"  revision 4 的 soul_prompt = {cur['snapshot']['soul_prompt']!r}")
        checks.append(("回滚成功", st == 200))
        checks.append(("回滚是追加（出现 revision 4 / rollback）",
                       [x["revision"] for x in v] == [4, 3, 2, 1]
                       and v[0]["change_kind"] == "rollback"))
        checks.append(("内容真的回去了", cur["snapshot"]["soul_prompt"] == "原始人格"))

        print("\n=== 被回滚掉的那版还在（历史不改写）===")
        _, v3 = call("GET", f"/agents/{aid}/versions/3")
        print(f"  revision 3 = {v3['snapshot']['soul_prompt']!r}")
        checks.append(("历史没被改写", v3["snapshot"]["soul_prompt"] == "第三版"))

        print("\n=== 再回滚到 3（证明可以回来）===")
        call("POST", f"/agents/{aid}/rollback", {"revision": 3})
        _, v5 = call("GET", f"/agents/{aid}/versions/5")
        print(f"  revision 5 = {v5['snapshot']['soul_prompt']!r}")
        checks.append(("回滚之后能再回来", v5["snapshot"]["soul_prompt"] == "第三版"))

        print("\n=== 不存在的 revision 该 404 ===")
        st, _ = call("POST", f"/agents/{aid}/rollback", {"revision": 999})
        checks.append(("不存在的 revision → 404", st == 404))

        print("\n=== 判定 ===")
        for name, passed in checks:
            print(f"  [{'OK' if passed else 'FAIL'}] {name}")
            ok = ok and passed
        return 0 if ok else 1
    finally:
        st, _ = call("DELETE", f"/agents/{aid}")
        print(f"\n清理: DELETE /agents/{aid} -> {st}")


if __name__ == "__main__":
    sys.exit(main())
