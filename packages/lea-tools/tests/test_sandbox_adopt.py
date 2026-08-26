"""沙箱跨副本认领 —— 钉住"lea-tools 是无状态中间层"这件事。

沙箱住在 Cube 里，`SandboxSessionManager._sessions` 只是一份**句柄缓存**。这里的
每条测试都在防同一类回归：有人把缓存重新当成真源。

那种回归的症状很难查：单副本下缓存永远命中，功能测试全绿；一扩容，建沙箱的副本
和后续 exec 的副本不是同一个，用户随机撞到 "sandbox session not found"，而沙箱明明
好好活着。重启也一样 —— dict 一清空，那个会话的沙箱此后谁都碰不到。

需要真 Cube 的那部分在 verify_adopt.py 里（metadata 过滤、envd 连通性、工作区连续性
这些 mock 不出来）。这个文件只测本地逻辑，不联网。

跑法：
    pytest tests/test_sandbox_adopt.py -q -s
"""

from __future__ import annotations

import asyncio

import pytest

from lea_tools.services import sandbox_session as ss
from lea_tools.services._cube_sandbox import SandboxLookupError


class FakeBackend:
    """够用的假后端 —— 只要能证明"用的是这一个"。"""

    def __init__(self, sandbox_id: str):
        self.sandbox_id = sandbox_id

    def run_code(self, code, language="python", timeout=60):
        class R:
            stdout = f"ran on {self.sandbox_id}"
            stderr = ""
            exit_code = 0
        r = R()
        r.stdout = f"ran on {self.sandbox_id}"
        return r


@pytest.fixture
def cube_env(monkeypatch):
    monkeypatch.setenv("SANDBOX_BACKEND", "cube")


def _patch_cube(monkeypatch, *, found, spy=None):
    """把 Cube 那两个入口换掉：查询 + 构造。"""
    def fake_find(session_id):
        if spy is not None:
            spy.append(session_id)
        if isinstance(found, Exception):
            raise found
        return found

    monkeypatch.setattr(
        "lea_tools.services._cube_sandbox.find_session_sandbox", fake_find
    )
    monkeypatch.setattr(
        "lea_tools.services._cube_sandbox.CubeSandbox",
        lambda session_id="", tenant_id="", sandbox_id=None: FakeBackend(sandbox_id),
    )


# ── 核心：缓存没命中要去问 Cube，而不是直接 404 ──────────────────

def test_exec_adopts_when_cache_misses(monkeypatch, cube_env):
    """全新的 manager（= 另一个副本 / 刚重启）也能 exec 已有会话。

    这一条挂了就等于 lea-tools 扩不了容。
    """
    _patch_cube(monkeypatch, found={"sandbox_id": "sbx-1", "tenant_id": "t1"})
    mgr = ss.SandboxSessionManager()
    assert mgr._sessions == {}, "前提：本副本没见过这个会话"

    r = asyncio.run(mgr.execute("sess-1", "print(1)"))

    assert "sbx-1" in r["stdout"], "没认领到已有沙箱"
    assert mgr._sessions["sess-1"].sandbox_id == "sbx-1", "认领后没回填缓存"


def test_create_reuses_instead_of_double_creating(monkeypatch, cube_env):
    """副本 B 收到 create 时要认领，不能再建一个。

    建了第二个的后果是同一会话两个 /workspace，用户看到的文件取决于请求落到谁手上。
    """
    _patch_cube(monkeypatch, found={"sandbox_id": "sbx-1", "tenant_id": "t1"})
    mgr = ss.SandboxSessionManager()

    def boom(*a, **kw):
        raise AssertionError("不该建新沙箱 —— Cube 上已经有一个了")

    monkeypatch.setattr(mgr, "_create_backend", boom)

    sess = asyncio.run(mgr.create(session_id="sess-1", tenant_id="t1"))
    assert sess.sandbox_id == "sbx-1"


def test_missing_sandbox_still_raises_key_error(monkeypatch, cube_env):
    """Cube 说"没有"时照旧 KeyError（API 层映射成 404）。

    认领这条路不能把"真的没有"也吞成成功。
    """
    _patch_cube(monkeypatch, found=None)
    mgr = ss.SandboxSessionManager()

    with pytest.raises(KeyError):
        asyncio.run(mgr.execute("sess-nope", "print(1)"))


# ── 查询失败 ≠ 没有 ──────────────────────────────────────────────

def test_lookup_failure_does_not_become_a_new_sandbox(monkeypatch, cube_env):
    """问不到 Cube 时必须往上抛（API 层 → 503），不能当成"没有"去建新的。

    当成"没有"的后果不是报错，是**静默泄漏**：Cube 恢复之后同一会话两个沙箱，
    两个都在跑，谁都不会被回收。
    """
    _patch_cube(monkeypatch, found=SandboxLookupError("cube down"))
    mgr = ss.SandboxSessionManager()

    def boom(*a, **kw):
        raise AssertionError("查询失败时不该建新沙箱")

    monkeypatch.setattr(mgr, "_create_backend", boom)

    with pytest.raises(SandboxLookupError):
        asyncio.run(mgr.create(session_id="sess-1", tenant_id="t1"))


# ── 缓存仍然要起作用 ────────────────────────────────────────────

def test_cache_hit_does_not_query_cube(monkeypatch, cube_env):
    """命中缓存就别问 Cube —— 每次 exec 多一次 HTTP 往返是实打实的延迟。"""
    spy = []
    _patch_cube(monkeypatch, found={"sandbox_id": "sbx-1", "tenant_id": "t1"}, spy=spy)
    mgr = ss.SandboxSessionManager()

    asyncio.run(mgr.execute("sess-1", "print(1)"))
    assert len(spy) == 1, "第一次该查"
    asyncio.run(mgr.execute("sess-1", "print(2)"))
    assert len(spy) == 1, f"命中缓存后又查了 Cube（{len(spy)} 次）"


# ── 非 cube 后端不受影响 ────────────────────────────────────────

def test_mock_backend_does_not_attempt_adoption(monkeypatch):
    """mock / e2b 后端没有远端实体可认，行为跟以前一样。"""
    monkeypatch.setenv("SANDBOX_BACKEND", "mock")
    spy = []
    _patch_cube(monkeypatch, found={"sandbox_id": "sbx-1", "tenant_id": "t1"}, spy=spy)
    mgr = ss.SandboxSessionManager()

    with pytest.raises(KeyError):
        asyncio.run(mgr.execute("sess-1", "print(1)"))
    assert spy == [], "mock 后端不该去问 Cube"


# ── 淘汰句柄 ≠ 杀沙箱 ───────────────────────────────────────────

def test_eviction_does_not_kill_the_sandbox(monkeypatch, cube_env):
    """本地缓存过期只丢句柄，不能 kill —— 沙箱可能正被别的副本用着。

    这条是整件事里最容易回退的：`destroy()` 就在手边，看起来正是该调的那个。
    调了的后果是把别人正在跑的沙箱杀掉，且只在多副本下偶发。
    """
    _patch_cube(monkeypatch, found={"sandbox_id": "sbx-1", "tenant_id": "t1"})
    mgr = ss.SandboxSessionManager()
    asyncio.run(mgr.execute("sess-1", "print(1)"))

    killed = []
    monkeypatch.setattr(mgr, "_kill_backend",
                        lambda sess: killed.append(sess.session_id))
    # 把它推成"本副本闲置很久"
    mgr._sessions["sess-1"].last_used_at -= ss.IDLE_TIMEOUT + 10

    mgr._evict_stale_handles()

    assert "sess-1" not in mgr._sessions, "句柄没被淘汰"
    assert killed == [], "淘汰缓存时杀了远端沙箱 —— 别的副本可能正在用它"


# ── 回收判据 ────────────────────────────────────────────────────

def _cube_rows(monkeypatch, rows):
    class Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return rows
    monkeypatch.setattr("lea_tools.services._cube_sandbox.httpx.get",
                        lambda *a, **kw: Resp())


def _row(sandbox_id, state, paused_minutes_ago, session_id="sess-x"):
    from datetime import datetime, timedelta, timezone
    from lea_tools.services._cube_sandbox import META_SESSION
    end_at = (datetime.now(timezone.utc)
              - timedelta(minutes=paused_minutes_ago)).isoformat().replace("+00:00", "Z")
    meta = {"cube.product": "cubebox"}
    if session_id:
        meta[META_SESSION] = session_id
    return {"sandboxID": sandbox_id, "state": state, "endAt": end_at, "metadata": meta}


def test_reaper_selects_only_our_paused_and_stale(monkeypatch):
    from lea_tools.services._cube_sandbox import list_reapable_sandboxes

    _cube_rows(monkeypatch, [
        _row("keep-running", "running", 999),        # running：绝不碰
        _row("keep-fresh", "paused", 5),             # 才 paused 5 分钟
        _row("keep-foreign", "paused", 999, None),   # 不是我们建的
        _row("reap-me", "paused", 120),              # 该回收
    ])

    got = {r["sandbox_id"] for r in list_reapable_sandboxes(3600)}
    assert got == {"reap-me"}, f"选错了：{got}"


def test_reaper_never_touches_running_sandboxes(monkeypatch):
    """running 的一律不动，哪怕看起来很老。

    判断一个 running 沙箱忙不忙需要本地视角，而多副本下没有哪个副本的视角是全的。
    误杀一个 running 沙箱 = 掐掉用户正在跑的代码。
    """
    from lea_tools.services._cube_sandbox import list_reapable_sandboxes

    _cube_rows(monkeypatch, [_row("busy", "running", 60 * 24 * 30)])
    assert list_reapable_sandboxes(1) == []


def test_reaper_tolerates_malformed_rows(monkeypatch):
    """字段缺失/格式怪异时跳过，不能让后台循环崩掉。

    崩了的话连句柄淘汰也一起停了 —— 一个回收 bug 会顺带废掉另一条清理路径。
    """
    from lea_tools.services._cube_sandbox import list_reapable_sandboxes, META_SESSION

    _cube_rows(monkeypatch, [
        {"sandboxID": "a", "state": "paused", "metadata": {META_SESSION: "s"}},  # 无 endAt
        {"sandboxID": "b", "state": "paused", "endAt": "not-a-date",
         "metadata": {META_SESSION: "s"}},
        {"state": "paused", "endAt": "2020-01-01T00:00:00Z",
         "metadata": {META_SESSION: "s"}},                                       # 无 id
        {},
        _row("ok", "paused", 120),
    ])
    got = {r["sandbox_id"] for r in list_reapable_sandboxes(60)}
    assert got == {"ok"}, f"容错有问题：{got}"
