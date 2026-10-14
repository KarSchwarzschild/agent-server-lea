"""用户上传原件的落地与物化 —— 路径安全、幂等、清洗规则。

这一层的 bug 有个共同特征：**症状指不到真因**。物化写了半个文件 → python-pptx 报
"文件损坏"；清洗规则两边不一致 → 模型拿到不存在的路径、报错说"文件不存在"；越界检查
用错函数 → 真实存在的文件报"路径越界"而不存在的报 404。所以这里逐条钉住。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "lea-tools" / "src"))

from lea_tools.services.file_stager import (  # noqa: E402
    UPLOAD_PREFIX,
    prune_session_uploads,
    safe_stage_name,
    upload_key,
)
from lea_tools.services.file_store import (  # noqa: E402
    SKILL_MOUNT,
    UPLOADS_MOUNT,
    NFSFileStore,
    SandboxFileStore,
)

# ── 挂载点常量必须跨服务一致 ──────────────────────────────────────

def test_uploads_mount_matches_go_side():
    """lea-agent 的 persistence.UploadsMount 硬编码了同一个字符串。

    对不上的后果：注入文本里给模型的路径跟真实挂载点不同，模型 open() 一个不存在的
    路径，而报错是"文件不存在"—— 看起来像物化失败，排查会绕到存储那边去。
    """
    assert UPLOADS_MOUNT == "/mnt/uploads"
    go_file = (
        Path(__file__).resolve().parents[1]
        / "packages" / "lea-agent" / "internal" / "persistence" / "attachments.go"
    )
    assert f'UploadsMount = "{UPLOADS_MOUNT}"' in go_file.read_text(encoding="utf-8")


def test_uploads_and_skills_mounts_are_distinct():
    """两个 RO 挂载点不能互为前缀 —— 否则 _resolve_readable 的分支判断会串。"""
    assert not UPLOADS_MOUNT.startswith(SKILL_MOUNT)
    assert not SKILL_MOUNT.startswith(UPLOADS_MOUNT)


# ── blob key：内容寻址 + 租户隔离 ─────────────────────────────────

def test_upload_key_is_tenant_scoped():
    """带 tenant 前缀，不是全局按 sha 寻址。

    跨租户共享 blob 会引入两个真问题：A 删文件让 B 的消失；以及"上传后发现已存在"
    变成存在性探测。而去重收益主要来自同一个班/同一门课，本来就在一个租户里。
    """
    sha = "a" * 64
    k1 = upload_key("tenant-1", sha, "pdf")
    k2 = upload_key("tenant-2", sha, "pdf")
    assert k1 != k2
    assert k1.startswith(f"{UPLOAD_PREFIX}/tenant-1/")
    assert sha in k1


def test_upload_key_normalizes_extension():
    sha = "b" * 64
    assert upload_key("t", sha, "PDF") == upload_key("t", sha, ".pdf")
    # 没扩展名也要能生成合法 key
    assert upload_key("t", sha, "") == f"{UPLOAD_PREFIX}/t/{sha}"


def test_upload_key_is_deterministic():
    """同内容同 key —— 去重和幂等重试都靠这条。"""
    sha = "c" * 64
    assert upload_key("t", sha, "pptx") == upload_key("t", sha, "pptx")


# ── 文件名清洗：路径穿越 + 原名保留 ──────────────────────────────

@pytest.mark.parametrize("evil", [
    "../../etc/passwd",
    "..\\..\\windows\\system32",
    "a/b/c.txt",
    "x\x00y.pdf",
    "con:.txt",
    "he<lo>.txt",
])
def test_safe_stage_name_never_escapes(evil):
    """清洗后必须是单个文件名 —— 它会被直接拼到 uploads/ 下面。"""
    got = safe_stage_name("d" * 64, evil)
    assert "/" not in got
    assert "\\" not in got
    assert "\x00" not in got
    assert not got.startswith(".")
    assert os.path.basename(got) == got


def test_safe_stage_name_keeps_original_name():
    """保留原名,这是 Assistants API 踩过的坑。

    它挂进沙箱的名字是 file ID（/mnt/data/file-p4zUlf6...），原名从不下发,于是挂多个
    文件时模型分不清哪个是哪个,只能靠额外注入对照表或让模型自己建 symlink 补救。
    路径里带原名,模型直接就能对上用户说的"那个课件"。
    """
    got = safe_stage_name("a3f19c7e" + "0" * 56, "第三章 卷积.pptx")
    assert "第三章 卷积" in got
    assert got.endswith(".pptx")
    assert got.startswith("a3f19c7e-")


def test_safe_stage_name_sha_prefix_prevents_collision():
    """同名不同内容不能互相覆盖。"""
    a = safe_stage_name("1" * 64, "报告.docx")
    b = safe_stage_name("2" * 64, "报告.docx")
    assert a != b


def test_safe_stage_name_truncates_on_char_boundary():
    """超长中文名按**字节**截断,且不能切碎一个多字节字符。

    切碎的后果是磁盘上出现非法 UTF-8 文件名 —— 之后 os.walk 和 json 序列化都会出
    奇怪的错,而错误信息完全指不到"上传时名字太长"这个真因。
    """
    got = safe_stage_name("e" * 64, "中" * 300 + ".pptx")
    encoded = got.encode("utf-8")
    assert len(encoded) < 255, "要能落在常见文件系统的单段上限内"
    # 关键：必须能无损解码回来
    assert encoded.decode("utf-8") == got
    assert got.endswith(".pptx")


def test_safe_stage_name_empty_and_dots():
    for bad in ("", ".", "..", "   "):
        got = safe_stage_name("f" * 64, bad)
        assert got == "f" * 8 + "-file", f"{bad!r} -> {got}"


def test_safe_stage_name_normalizes_fullwidth():
    """NFKC 归一：全角和半角折叠成同一形式。

    不归一的话同一个名字在不同输入法下产生不同字节序列 —— 用户看到一样,磁盘上是
    两个文件,而"我明明传过了"这种反馈查不出原因。
    """
    a = safe_stage_name("0" * 64, "ｒｅｐｏｒｔ.pdf")
    b = safe_stage_name("0" * 64, "report.pdf")
    assert a == b


# ── 读侧路径解析：两个 RO 挂载点 ──────────────────────────────────

@pytest.fixture
def session_tree(tmp_path):
    """造一个会话目录：workspace / skills / uploads 三兄弟。"""
    base = tmp_path / "tenants" / "t1" / "sessions" / "s1"
    ws = base / "workspace"
    ws.mkdir(parents=True)
    (base / "skills").mkdir()
    up = base / "uploads"
    up.mkdir()
    (ws / "out.txt").write_text("产物", encoding="utf-8")
    (up / "abc123-课件.pptx").write_bytes(b"PK\x03\x04fake")
    return base


def test_uploads_readable_through_mount_prefix(session_tree):
    """模型用沙箱路径读上传件 —— 注入文本给它的就是这个形式。"""
    store = NFSFileStore(session_tree / "workspace")
    data = store.read(f"{UPLOADS_MOUNT}/abc123-课件.pptx")
    assert data == b"PK\x03\x04fake"


def test_uploads_not_writable(session_tree):
    """原件只读。

    两个理由：改坏了没法重来；以及 /workspace 才会被扫描推 S3 进文件抽屉,写别处
    产物就"消失"了（OpenAI 那边原地覆盖上传件导致产物拿不到,是同一类问题）。
    """
    store = NFSFileStore(session_tree / "workspace")
    with pytest.raises(PermissionError):
        store.write(f"{UPLOADS_MOUNT}/abc123-课件.pptx", b"tampered")


@pytest.mark.parametrize("escape", [
    f"{UPLOADS_MOUNT}/../workspace/out.txt",
    f"{UPLOADS_MOUNT}/../../../../etc/passwd",
    f"{UPLOADS_MOUNT}/../skills/x",
])
def test_uploads_escape_rejected(session_tree, escape):
    store = NFSFileStore(session_tree / "workspace")
    with pytest.raises(PermissionError):
        store.read(escape)


def test_skills_mount_still_works(session_tree):
    """加 uploads 分支不能破坏 skills 那条已有路径。"""
    (session_tree / "skills" / "demo").mkdir()
    (session_tree / "skills" / "demo" / "SKILL.md").write_text("正文", encoding="utf-8")
    store = NFSFileStore(session_tree / "workspace")
    assert store.read(f"{SKILL_MOUNT}/demo/SKILL.md").decode() == "正文"


def test_workspace_relative_paths_unaffected(session_tree):
    """普通相对路径不受两个前缀影响。"""
    store = NFSFileStore(session_tree / "workspace")
    assert store.read("out.txt").decode("utf-8") == "产物"


def test_uploads_resolution_follows_symlink_dir(tmp_path):
    """挂载点解析用 normpath 而不是 resolve —— symlink 是正常部署结构。

    这条是从 skills 那边的实测教训搬过来的：NFS 上目录可能是符号链接,resolve 会跟随
    它、于是真实文件落在挂载根之外、比对必然失败。症状极具误导性：**不存在**的文件报
    404,**真实存在**的文件报"路径越界"。
    """
    real = tmp_path / "real_uploads"
    real.mkdir()
    (real / "x.bin").write_bytes(b"data")

    base = tmp_path / "tenants" / "t1" / "sessions" / "s1"
    (base / "workspace").mkdir(parents=True)
    link = base / "uploads"
    try:
        link.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("这个环境不允许建符号链接（Windows 需要开发者模式）")

    store = NFSFileStore(base / "workspace")
    assert store.read(f"{UPLOADS_MOUNT}/x.bin") == b"data"


# ── 沙箱 store：路径原样透传 ─────────────────────────────────────

def test_sandbox_store_passes_uploads_through():
    """沙箱里 /mnt/uploads 就是真实路径（RO bind mount）,不需要映射。"""
    assert SandboxFileStore._check_readable(f"{UPLOADS_MOUNT}/a.pptx") == f"{UPLOADS_MOUNT}/a.pptx"
    assert SandboxFileStore._check_readable("rel/path.txt") == "/workspace/rel/path.txt"


def test_sandbox_store_blocks_uploads_escape():
    with pytest.raises(PermissionError):
        SandboxFileStore._check_readable(f"{UPLOADS_MOUNT}/../../etc/passwd")


# ── host-mount 配置 ─────────────────────────────────────────────

def test_host_mount_includes_uploads_readonly(monkeypatch):
    """沙箱必须挂到 uploads,而且是 RO。少了这一条,"改文件"整条路不通。"""
    import json

    from lea_tools.services import nfs_provisioner

    monkeypatch.setenv("NFS_ROOT", "/data/lea")
    nfs_provisioner.get_nfs_settings.cache_clear()

    mounts = json.loads(nfs_provisioner.build_host_mount("t1", "s1"))
    by_path = {m["mountPath"]: m for m in mounts}

    assert UPLOADS_MOUNT in by_path, "uploads 没挂进沙箱,模型读不到上传的文件"
    assert by_path[UPLOADS_MOUNT]["readOnly"] is True, "原件必须只读"
    assert by_path[UPLOADS_MOUNT]["hostPath"].endswith("/sessions/s1/uploads")

    # 不能动到原有两条
    assert by_path["/workspace"]["readOnly"] is False
    assert by_path[SKILL_MOUNT]["readOnly"] is True


def test_uploads_is_sibling_of_workspace(monkeypatch):
    """uploads 必须在 workspace **外面**。

    在里面的话 _cube_sandbox 收尾扫 /workspace 推 S3 时会把上传件当产物再传一遍：
    同一份字节存两遍,而且污染文件抽屉（用户看到自己刚传的文件出现在"AI 生成的产物"里）。
    """
    import json

    from lea_tools.services import nfs_provisioner

    monkeypatch.setenv("NFS_ROOT", "/data/lea")
    nfs_provisioner.get_nfs_settings.cache_clear()

    mounts = {m["mountPath"]: m["hostPath"] for m in
              json.loads(nfs_provisioner.build_host_mount("t1", "s1"))}
    assert not mounts[UPLOADS_MOUNT].startswith(mounts["/workspace"] + "/")
    assert Path(mounts[UPLOADS_MOUNT]).parent == Path(mounts["/workspace"]).parent


def test_ensure_session_dir_creates_uploads(tmp_path, monkeypatch):
    from lea_tools.services import nfs_provisioner

    monkeypatch.setenv("NFS_ROOT", str(tmp_path))
    nfs_provisioner.get_nfs_settings.cache_clear()

    base = nfs_provisioner.ensure_session_dir("t1", "s1")
    assert base is not None
    for sub in ("workspace", "skills", "uploads"):
        assert (base / sub).is_dir(), f"{sub} 没建出来"


# ── 清理 ───────────────────────────────────────────────────────

def test_prune_session_uploads(tmp_path, monkeypatch):
    """会话清理时删掉 NFS 副本。blob 在 OBS 里还有,所以这是安全的。"""
    from lea_tools.services import file_stager

    monkeypatch.setenv("NFS_ROOT", str(tmp_path))
    file_stager.get_nfs_settings.cache_clear()

    up = tmp_path / "tenants" / "t1" / "sessions" / "s1" / "uploads"
    up.mkdir(parents=True)
    (up / "a.bin").write_bytes(b"x" * 100)
    (up / "b.bin").write_bytes(b"y" * 50)

    assert file_stager.prune_session_uploads("t1", "s1") == 150
    assert not up.exists()
    # 幂等：再删一次不报错
    assert prune_session_uploads("t1", "s1") == 0


def test_uploads_bytes_counts_current_usage(tmp_path, monkeypatch):
    """配额判定靠现算,不维护计数器 —— 计数器在多节点下要处理并发递增和删除对账,
    漂移的表现是配额莫名其妙变紧、只能手动清。"""
    from lea_tools.services import file_stager

    monkeypatch.setenv("NFS_ROOT", str(tmp_path))
    file_stager.get_nfs_settings.cache_clear()

    stager = file_stager.FileStager()
    assert stager.uploads_bytes("t1", "s1") == 0

    up = tmp_path / "tenants" / "t1" / "sessions" / "s1" / "uploads"
    up.mkdir(parents=True)
    (up / "a.bin").write_bytes(b"z" * 2048)
    assert stager.uploads_bytes("t1", "s1") == 2048
