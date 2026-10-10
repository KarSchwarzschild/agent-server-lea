"""Agent 文件工具的并发、覆盖与逃逸测试。

钉住三类行为，它们都属于"跑一次看不出问题、偶发时极难定位"那一档：

1. **撕裂读** —— 并发的读者永远看到完整的某一版，不会读到写了一半的文件。
   靠 NFSFileStore.write 的原子替换（临时文件 + os.replace）保证。
2. **丢失更新 / 盲写** —— 两个 edit 都基于 v1，后写者必须报冲突而不是静默抹掉
   先写者。靠 (mtime_ns, size) 新鲜度校验保证。
3. **路径逃逸** —— 包括 `../` 和"兄弟目录名以 workspace 开头"这种前缀匹配漏洞。

跑法：
    pytest tests/test_file_tools.py -q
    python tests/test_file_tools.py        # 不装 pytest 也能跑
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lea_tools.services.file_store import (  # noqa: E402
    NFSFileStore,
    SandboxFileStore,
    StaleWriteError,
    Stat,
)

TENANT, SESSION = "tenant-test", "sess-filetools"


@pytest.fixture()
def store(tmp_path: Path) -> NFSFileStore:
    ws = tmp_path / "workspace"
    ws.mkdir()
    # 同级的 workspace-evil：字符串前缀匹配会把它当成 workspace 内部，
    # is_relative_to 不会。逃逸测试靠它。
    evil = tmp_path / "workspace-evil"
    evil.mkdir()
    (evil / "secret").write_text("pwned")
    return NFSFileStore(ws)


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    """真实 ASGI 栈上的 files 路由，用来验状态码语义。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setenv("FILE_STORE", "nfs")
    monkeypatch.setenv("NFS_ROOT", str(tmp_path))
    ws = tmp_path / "tenants" / TENANT / "sessions" / SESSION / "workspace"
    ws.mkdir(parents=True)

    from lea_tools.api.files import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def q(**extra) -> dict:
    return {"session_id": SESSION, "tenant_id": TENANT, **extra}


# ── 1 · 撕裂读 ────────────────────────────────────────────────

def test_concurrent_read_never_sees_partial_write(store: NFSFileStore):
    """一边反复覆写大文件，一边反复读，读到的内容必须是完整的某一版。

    每一版填充同一个字符，所以"内容不是单一字符重复"就等于读到了拼接的中间态。
    非原子写（open+truncate+write）在这个测试下会失败。
    """
    size = 512 * 1024
    versions = {c: (c * size).encode() for c in "ABCD"}
    st = store.write("big.bin", versions["A"])

    stop = threading.Event()
    bad: list[str] = []

    def writer():
        nonlocal st
        try:
            for _ in range(40):
                for c in "BCDA":
                    st = store.write("big.bin", versions[c], expect=st)
        finally:
            stop.set()

    def reader():
        while not stop.is_set():
            try:
                data = store.read("big.bin")
            except FileNotFoundError:
                # 原子替换不会让文件短暂消失；真出现就是实现退化了
                bad.append("file vanished mid-write")
                continue
            if len(data) != size or len(set(data)) != 1:
                bad.append(f"torn read: len={len(data)} distinct_bytes={len(set(data))}")

    threads = [threading.Thread(target=writer)] + [
        threading.Thread(target=reader) for _ in range(3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not bad, bad[:5]


def test_failed_write_leaves_no_tmp_file(store: NFSFileStore):
    """写入过程中抛异常时不能把临时文件留在 workspace 里。

    留下的话它会出现在用户的文件抽屉里，还会被 list 当成产物。
    """
    ws = store._ws

    class Boom(bytes):
        pass

    with pytest.raises(TypeError):
        store.write("x.txt", object())  # type: ignore[arg-type]

    assert [p.name for p in ws.rglob("*") if ".tmp" in p.name] == []


# ── 2 · 丢失更新 / 盲写 ──────────────────────────────────────

def test_blind_overwrite_is_refused(store: NFSFileStore):
    store.write("a.txt", b"v1")
    with pytest.raises(StaleWriteError):
        store.write("a.txt", b"v2")
    assert store.read("a.txt") == b"v1"


def test_fresh_overwrite_succeeds(store: NFSFileStore):
    st = store.write("a.txt", b"v1")
    store.write("a.txt", b"v2", expect=st)
    assert store.read("a.txt") == b"v2"


def test_stale_overwrite_is_refused(store: NFSFileStore):
    """经典丢失更新：A 和 B 都基于 v1，B 必须被拒。"""
    v1 = store.write("a.txt", b"v1")
    store.write("a.txt", b"A-edit", expect=v1)  # A 先写成功
    with pytest.raises(StaleWriteError):
        store.write("a.txt", b"B-edit", expect=v1)  # B 仍拿着 v1
    assert store.read("a.txt") == b"A-edit", "A 的改动不能被静默抹掉"


def test_consecutive_self_writes_are_allowed(store: NFSFileStore):
    """模型改自己刚写的文件不能被判过期 —— 这是正常工作流，不是冲突。"""
    st = store.write("a.txt", b"draft")
    st = store.write("a.txt", b"draft v2", expect=st)
    st = store.write("a.txt", b"draft v3", expect=st)
    assert store.read("a.txt") == b"draft v3"


def test_same_second_writes_have_distinct_mtime_ns(store: NFSFileStore):
    """用 mtime_ns 而非秒级 mtime 的理由：同秒两次写必须可区分。

    若退回秒级精度，快速连续的两次写会得到相同 mtime，新鲜度校验就把"已被改动"
    误判成"没变"—— 正好放过要防的那个场景。
    """
    a = store.write("t.txt", b"1")
    b = store.write("t.txt", b"2", expect=a)
    assert a.mtime_ns != b.mtime_ns


def test_expect_on_missing_file_is_refused(store: NFSFileStore):
    """声称基于某一版、但文件已不存在 —— 可能被删了，不能当新建处理。"""
    with pytest.raises(StaleWriteError):
        store.write("ghost.txt", b"x", expect=Stat(size=1, mtime_ns=123))


def test_create_only_refuses_existing(store: NFSFileStore):
    store.write("a.txt", b"v1")
    with pytest.raises(StaleWriteError):
        store.write("a.txt", b"v2", allow_create_only=True)


def test_parallel_writers_only_one_wins_per_version(store: NFSFileStore):
    """N 个线程同时基于同一版覆写：恰好一个成功，其余全部报冲突。

    这是"跨子代理并行写同一文件"的最小复现 —— 不能出现两个都成功（后者覆盖前者）。
    """
    base = store.write("shared.txt", b"base")
    ok: list[int] = []
    conflicts: list[int] = []
    lock = threading.Lock()

    def attempt(i: int):
        try:
            store.write("shared.txt", f"writer-{i}".encode(), expect=base)
        except StaleWriteError:
            with lock:
                conflicts.append(i)
        else:
            with lock:
                ok.append(i)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(ok) == 1, f"应恰好一个成功，实际 {len(ok)}：{ok}"
    assert len(conflicts) == 7
    assert store.read("shared.txt") == f"writer-{ok[0]}".encode()


def test_concurrent_edits_never_lose_a_change(store: NFSFileStore):
    """N 个线程各改文件里自己那一行，全部成功时每一处改动都必须留在文件里。

    这是 edit 的核心保证。若读-改-写不在同一把锁内，两个 edit 会都基于同一版，
    后写者把前写者的改动静默抹掉 —— 文件里会少几处，而两个调用都返回成功。
    """
    n = 8
    store.write("code.py", "".join(f"v{i} = 0\n" for i in range(n)).encode())

    failed: list[str] = []
    lock = threading.Lock()

    def attempt(i: int):
        for _ in range(20):  # 冲突就重试，模拟模型重读后再改
            try:
                store.edit("code.py", f"v{i} = 0", f"v{i} = {i + 1}")
                return
            except (StaleWriteError, ValueError):
                continue
        with lock:
            failed.append(f"v{i} 重试 20 次仍未成功")

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not failed, failed
    text = store.read("code.py").decode()
    lost = [i for i in range(n) if f"v{i} = {i + 1}" not in text]
    assert not lost, f"这些改动被静默抹掉了：{lost}\n{text}"


def test_edit_refuses_ambiguous_and_missing(store: NFSFileStore):
    """匹配数不为 1 时抛 ValueError(次数)，不猜着改。"""
    store.write("d.txt", b"aa\naa\n")
    with pytest.raises(ValueError) as ei:
        store.edit("d.txt", "aa", "bb")
    assert str(ei.value) == "2"
    assert store.read("d.txt") == b"aa\naa\n", "报错时不能有任何改动落盘"

    with pytest.raises(ValueError) as ei:
        store.edit("d.txt", "nowhere", "x")
    assert str(ei.value) == "0"


# ── 3 · 路径逃逸 ─────────────────────────────────────────────

@pytest.mark.parametrize("bad_path", [
    "../../etc/passwd",
    "../workspace-evil/secret",   # 前缀匹配漏洞：workspace-evil 以 workspace 开头
    "sub/../../workspace-evil/secret",
])
def test_read_refuses_escape(store: NFSFileStore, bad_path: str):
    with pytest.raises(PermissionError):
        store.read(bad_path)


@pytest.mark.parametrize("bad_path", [
    "../../tmp/evil",
    "../workspace-evil/secret",
])
def test_write_refuses_escape(store: NFSFileStore, bad_path: str):
    with pytest.raises(PermissionError):
        store.write(bad_path, b"pwned")


# 模型跑 Bash 工具时看到的是沙箱里真实的绝对路径 /workspace/x（nfs_provisioner
# 的 mountPath），跟 Write/Edit/Read 期望的"相对于会话 workspace 根"的相对
# 路径是两套约定——模型很容易把两者搞混，传一个 "/workspace/x" 过来。这几个
# 测试钉住"两种写法必须等价"，而不是让模型自己想起来要去掉这段前缀（那句
# "路径越界"报错完全看不出该怎么改，实测会让模型反复重试白烧 ReAct 轮次）。
def test_write_accepts_workspace_absolute_prefix(store: NFSFileStore):
    store.write("/workspace/slides/a.js", b"content")
    assert store.read("slides/a.js") == b"content", "绝对路径和相对路径必须落到同一个文件"


def test_read_accepts_workspace_absolute_prefix(store: NFSFileStore):
    store.write("slides/a.js", b"content")
    assert store.read("/workspace/slides/a.js") == b"content"


def test_workspace_prefix_normalization_does_not_loosen_other_absolute_paths(store: NFSFileStore):
    """只认 /workspace 这一个前缀——别的绝对路径（不管是不是长得像）依旧要拒。"""
    with pytest.raises(PermissionError):
        store.write("/etc/passwd", b"pwned")
    with pytest.raises(PermissionError):
        store.read("/etc/passwd")


# SandboxFileStore 是走 envd 直接操作沙箱里 /workspace 的第二条路径（跟
# NFSFileStore 分开实现），同样的"模型把绝对路径带过来"的问题在这边独立发生
# 过一次——_check 是纯字符串处理的 staticmethod，不需要真沙箱后端就能测。
def test_sandbox_check_accepts_workspace_absolute_prefix():
    assert SandboxFileStore._check("/workspace/slides/a.js") == "/workspace/slides/a.js"
    assert SandboxFileStore._check("slides/a.js") == "/workspace/slides/a.js"


def test_sandbox_check_still_refuses_other_absolute_paths():
    with pytest.raises(PermissionError):
        SandboxFileStore._check("/etc/passwd")


def test_write_creates_nested_dirs(store: NFSFileStore):
    store.write("output/deep/r.csv", b"a,b\n")
    assert store.read("output/deep/r.csv") == b"a,b\n"


# ── 3b · skill 挂载点（只读放行） ─────────────────────────────
#
# skill 目录是 workspace 的**同级**（sessions/{sid}/skills），挂进沙箱是 /opt/skills。
# 模型看到的路径就是后者，所以读侧接受它并映射到宿主目录。放行只在读路径上 ——
# 那是投递进去的平台资源，被 agent 改掉会影响后续会话（bundle 按 sha 缓存复用）。

@pytest.fixture()
def store_with_skill(tmp_path: Path) -> NFSFileStore:
    ws = tmp_path / "workspace"
    ws.mkdir()
    ref = tmp_path / "skills" / "pptx-generator" / "references"
    ref.mkdir(parents=True)
    (ref / "slide-types.md").write_text("# 版式清单\n", encoding="utf-8")
    # 同级的 skills-evil：前缀匹配会把它当成 skills 内部
    evil = tmp_path / "skills-evil"
    evil.mkdir()
    (evil / "secret").write_text("pwned")
    return NFSFileStore(ws)


def test_read_reaches_skill_mount(store_with_skill: NFSFileStore):
    got = store_with_skill.read("/opt/skills/pptx-generator/references/slide-types.md")
    # 不断言行尾：fixture 用 write_text，Windows 上会写成 \r\n
    assert got.decode("utf-8").strip() == "# 版式清单"


def test_skill_mount_is_not_writable(store_with_skill: NFSFileStore):
    """写侧必须仍走严格解析 —— 否则 agent 能改坏平台投递的 skill。"""
    with pytest.raises(PermissionError):
        store_with_skill.write("/opt/skills/pptx-generator/SKILL.md", b"pwned")
    with pytest.raises(PermissionError):
        store_with_skill.edit("/opt/skills/pptx-generator/SKILL.md", "a", "b")


@pytest.mark.parametrize("bad_path", [
    "/opt/skills/../../etc/passwd",
    "/opt/skills/../skills-evil/secret",   # 前缀匹配漏洞的 skills 版本
    "/opt/skills/x/../../skills-evil/secret",
])
def test_skill_mount_refuses_escape(store_with_skill: NFSFileStore, bad_path: str):
    with pytest.raises(PermissionError):
        store_with_skill.read(bad_path)


@pytest.mark.skipif(os.name == "nt", reason="Windows 建 symlink 需要管理员权限")
def test_skill_mount_follows_symlinked_bundle(tmp_path: Path):
    """skill 目录里的条目是符号链接时也要读得到。

    这是真机上暴露出来的：ensure_cached 把 bundle 解压到 `{root}/cache/skills/<sha>/`，
    投递再进 session 目录，那条链上出现 symlink 是正常部署结构。而越界检查若用
    Path.resolve()，真实文件会被解析到 cache 目录、落在 skills_root 之外 —— 于是
    **同一个目录下不存在的文件报"文件不存在"、真实存在的文件报"路径越界"**。
    症状看起来像投递失败，实际是检查把正常路径拒了。
    """
    ws = tmp_path / "workspace"
    ws.mkdir()
    # 模拟 cache：真实内容在别处，session 的 skills 目录里放一个链接
    cache = tmp_path / "cache" / "skills" / "deadbeef"
    cache.mkdir(parents=True)
    (cache / "SKILL.md").write_text("# 缓存里的正文\n", encoding="utf-8")
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "pdf").symlink_to(cache, target_is_directory=True)

    store = NFSFileStore(ws)
    got = store.read("/opt/skills/pdf/SKILL.md")
    assert got.decode("utf-8").strip() == "# 缓存里的正文"
    # 逃逸仍要挡住 —— 放行 symlink 不等于放行 `..`
    with pytest.raises(PermissionError):
        store.read("/opt/skills/pdf/../../etc/passwd")


def test_escape_errors_carry_marker_for_http_layer(store_with_skill: NFSFileStore):
    """越界抛的 PermissionError 必须带 "escapes"。

    files.py 的 read 端点靠这个标记把「路径越界」和「文件系统 EACCES」分开报 —— 两者
    都是 PermissionError，但一个是模型该换路径、一个是运维该修 owner/mode。原先一个
    except 全接了，于是权限问题显示成路径穿越，实测把排查带偏了好几轮。

    这条契约靠字符串匹配，很脆，所以钉住。
    """
    for bad in ["../../etc/passwd", "/opt/skills/../../etc/passwd"]:
        with pytest.raises(PermissionError, match="escapes"):
            store_with_skill.read(bad)


def test_skill_prefix_does_not_shadow_workspace_dir(store_with_skill: NFSFileStore):
    """workspace 里真有一个 skills/ 目录时，两者不能互相遮蔽。

    这是用绝对前缀 /opt/skills 而不是相对 skills/ 的理由：相对形式下这两个路径
    会是同一个字符串。
    """
    store_with_skill.write("skills/mine.txt", b"local")
    assert store_with_skill.read("skills/mine.txt") == b"local"
    # 绝对前缀仍解析到同级的 skills 目录，不是 workspace/skills
    with pytest.raises(FileNotFoundError):
        store_with_skill.read("/opt/skills/mine.txt")


# ── 4 · HTTP 层状态码语义 ────────────────────────────────────

def test_endpoint_status_codes(client):
    """状态码要能让模型分辨"重试能成"和"重试也不行"。

    409 = 并发冲突，重读后重试可成功
    422 = 匹配不唯一/找不到，要改参数
    403 = 越界   404 = 不存在   400 = 参数非法
    """
    r = client.post("/api/files/write", json=q(path="r.md", content="line A\nline B\n"))
    assert r.status_code == 200
    st = client.get("/api/files/read", params=q(path="r.md")).json()["stat"]

    assert client.post("/api/files/write", json=q(path="r.md", content="x")).status_code == 409
    assert client.post("/api/files/write", json=q(
        path="r.md", content="line A\nline B\nline C\n",
        expect_mtime_ns=st["mtime_ns"], expect_size=st["size"],
    )).status_code == 200
    # 拿旧 stat 再写 —— 过期
    assert client.post("/api/files/write", json=q(
        path="r.md", content="y", expect_mtime_ns=st["mtime_ns"], expect_size=st["size"],
    )).status_code == 409

    assert client.post("/api/files/edit", json=q(
        path="r.md", old_string="line B", new_string="line B!",
    )).status_code == 200
    assert client.post("/api/files/edit", json=q(
        path="r.md", old_string="nowhere", new_string="x",
    )).status_code == 422

    client.post("/api/files/write", json=q(path="dup.txt", content="aa\naa\n"))
    r = client.post("/api/files/edit", json=q(path="dup.txt", old_string="aa", new_string="bb"))
    assert r.status_code == 422
    assert "2" in r.json()["detail"], "报错要说清出现了几次"

    assert client.get("/api/files/read", params=q(path="../../etc/passwd")).status_code == 403
    assert client.get("/api/files/read", params=q(path="ghost.txt")).status_code == 404
    assert client.post("/api/files/edit", json=q(
        path="ghost.txt", old_string="a", new_string="b",
    )).status_code == 404
    # 写侧不容忍空 tenant（/list 容忍，但那是只读的）
    assert client.get("/api/files/read", params={
        "session_id": SESSION, "tenant_id": "", "path": "r.md",
    }).status_code == 400


def test_edit_applies_at_the_right_place(client):
    """edit 改的必须是唯一匹配那一处，不能是"第一处"。"""
    client.post("/api/files/write", json=q(path="c.py", content="x = 1\ny = 1\nz = 1\n"))
    r = client.post("/api/files/edit", json=q(path="c.py", old_string="y = 1", new_string="y = 42"))
    assert r.status_code == 200
    assert client.get("/api/files/read", params=q(path="c.py")).json()["content"] == "x = 1\ny = 42\nz = 1\n"


def test_binary_file_is_rejected_with_guidance(client):
    """二进制文件报 415 并指路，而不是抛 UnicodeDecodeError 500。"""
    from lea_tools.services.file_store import get_file_store
    get_file_store(TENANT, SESSION).write("logo.png", b"\x89PNG\r\n\x1a\n\x00\xff")
    r = client.get("/api/files/read", params=q(path="logo.png"))
    assert r.status_code == 415
    assert "code_interpreter" in r.json()["detail"]


if __name__ == "__main__":
    # Windows 终端默认 GBK，直接跑脚本时强制 UTF-8 输出（否则中文断言信息看不清）。
    # 只在这里做 —— 在模块顶层换 sys.stdout 会跟 pytest 的 capture 打架，
    # 收尾时报 "I/O operation on closed file" 且一个测试都跑不了。
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    sys.exit(pytest.main([__file__, "-q"]))
