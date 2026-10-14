"""上传三步链路的端到端验收 —— 用假 OBS 打真 HTTP 端点。

## 为什么要打真端点，不只单测 FileStager

这条链路的 bug 大多在**接缝**上，而不在单个函数里：路由前缀拼错、请求体字段名不一致、
异常映射成了错的状态码。这些在单测里都看不见 —— 而它们的症状是"上传成功了但沙箱里
没文件"，排查要横跨两个服务。

假 OBS 只替掉 boto3 client（put/head/download/get 四个方法），其余全是真代码：真的
FastAPI 路由、真的 multipart 解析、真的落盘、真的 os.replace。
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "lea-tools" / "src"))

from lea_tools.api.files import router as files_router  # noqa: E402
from lea_tools.services import file_stager  # noqa: E402
from lea_tools.services.file_store import UPLOADS_MOUNT  # noqa: E402


class FakeOBS:
    """内存版 S3。只实现这条链路用到的四个操作。"""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.meta: dict[str, dict] = {}
        self.put_calls = 0
        self.download_calls = 0

    def _missing(self, key):
        return ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "not found"}}, "HeadObject"
        )

    def put_object(self, Bucket, Key, Body, ContentType=None, Metadata=None):
        self.objects[Key] = Body
        self.meta[Key] = {"ContentType": ContentType, "Metadata": Metadata or {}}
        self.put_calls += 1
        return {}

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise self._missing(Key)
        return {"ContentLength": len(self.objects[Key]),
                "ContentType": self.meta[Key].get("ContentType")}

    def download_fileobj(self, Bucket, Key, fileobj):
        if Key not in self.objects:
            raise self._missing(Key)
        fileobj.write(self.objects[Key])
        self.download_calls += 1

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise self._missing(Key)
        import io
        return {"Body": io.BytesIO(self.objects[Key]),
                "ContentType": self.meta[Key].get("ContentType")}


@pytest.fixture
def obs(monkeypatch):
    fake = FakeOBS()

    def _ensure(self):
        self._client = fake
        self._bucket = "test-bucket"

    monkeypatch.setattr(file_stager.FileStager, "_ensure_client", _ensure)
    return fake


@pytest.fixture
def nfs(tmp_path, monkeypatch):
    """造出会话目录 —— stage 要求它已存在（否则说明没共享存储）。"""
    monkeypatch.setenv("NFS_ROOT", str(tmp_path))
    monkeypatch.setenv("NFS_WORKSPACE_QUOTA_MB", "10")
    file_stager.get_nfs_settings.cache_clear()

    from lea_tools.api import files as files_api
    files_api.get_nfs_settings.cache_clear()

    base = tmp_path / "tenants" / "tenant-1" / "sessions" / "sess-abc123"
    (base / "workspace").mkdir(parents=True)
    (base / "uploads").mkdir()
    return base


@pytest.fixture
def client(obs, nfs):
    app = FastAPI()
    app.include_router(files_router)
    return TestClient(app)


PPTX_BYTES = b"PK\x03\x04" + b"fake pptx payload" * 50
PPTX_SHA = hashlib.sha256(PPTX_BYTES).hexdigest()


def _upload(client, name="课件.pptx", data=PPTX_BYTES, sha=None, tenant="tenant-1"):
    return client.post(
        "/api/files/uploads/blob",
        files={"file": (name, data, "application/octet-stream")},
        data={"tenant_id": tenant, "sha256": sha if sha is not None else ""},
    )


# ── 完整链路 ────────────────────────────────────────────────────

def test_full_upload_then_stage(client, obs, nfs):
    """blob → stage → 文件真的落在会话 uploads/ 下，路径跟返回的一致。"""
    r = _upload(client)
    assert r.status_code == 200, r.text
    blob = r.json()
    assert blob["sha256"] == PPTX_SHA
    assert blob["file_key"] == f"uploads/tenant-1/{PPTX_SHA}.pptx"
    assert blob["staged_name"].startswith(PPTX_SHA[:8] + "-")
    assert "课件" in blob["staged_name"], "原名要保留，模型靠它对上用户说的文件"

    r2 = client.post("/api/files/uploads/stage", json={
        "tenant_id": "tenant-1", "session_id": "sess-abc123",
        "file_key": blob["file_key"], "dest_name": blob["staged_name"],
    })
    assert r2.status_code == 200, r2.text
    staged = r2.json()

    # 沙箱路径必须是注入文本会用的那个形式
    assert staged["sandbox_path"] == f"{UPLOADS_MOUNT}/{blob['staged_name']}"
    assert staged["size"] == len(PPTX_BYTES)

    # 字节真的落盘了，而且内容一致
    on_disk = nfs / "uploads" / blob["staged_name"]
    assert on_disk.is_file()
    assert on_disk.read_bytes() == PPTX_BYTES


def test_staged_file_is_readonly_on_host(client, obs, nfs):
    """宿主侧也收紧 mode —— RO 挂载是一层，文件权限是第二层。"""
    import os
    import stat

    blob = _upload(client).json()
    client.post("/api/files/uploads/stage", json={
        "tenant_id": "tenant-1", "session_id": "sess-abc123",
        "file_key": blob["file_key"], "dest_name": blob["staged_name"],
    })
    mode = (nfs / "uploads" / blob["staged_name"]).stat().st_mode
    assert not (mode & stat.S_IWUSR) or os.name == "nt", "应去掉写位（Windows 语义不同，跳过）"


def test_no_staging_leftovers(client, obs, nfs):
    """临时文件不能留在 uploads/ 里 —— 它会被挂进沙箱，模型 ls 到 .staging-xxx 会困惑。"""
    blob = _upload(client).json()
    client.post("/api/files/uploads/stage", json={
        "tenant_id": "tenant-1", "session_id": "sess-abc123",
        "file_key": blob["file_key"], "dest_name": blob["staged_name"],
    })
    names = [p.name for p in (nfs / "uploads").iterdir()]
    assert not any(n.startswith(".staging-") for n in names), names


# ── 幂等 ────────────────────────────────────────────────────────

def test_blob_dedupes_same_content(client, obs):
    """同内容第二次上传不重复 PUT —— 一个班 40 个人传同一份课件只占一个对象。"""
    _upload(client)
    assert obs.put_calls == 1
    _upload(client)
    assert obs.put_calls == 1, "同 key 已存在且大小一致，应跳过"


def test_blob_isolates_tenants(client, obs):
    """同内容跨租户是两个对象 —— 避免 A 删文件让 B 的消失、以及存在性探测。"""
    k1 = _upload(client, tenant="tenant-1").json()["file_key"]
    k2 = _upload(client, tenant="tenant-2").json()["file_key"]
    assert k1 != k2
    assert obs.put_calls == 2


def test_stage_skips_redownload(client, obs, nfs):
    """已物化过就不再下载。会话重建、Drop 后重来都会走到这条。"""
    blob = _upload(client).json()
    body = {"tenant_id": "tenant-1", "session_id": "sess-abc123",
            "file_key": blob["file_key"], "dest_name": blob["staged_name"]}
    client.post("/api/files/uploads/stage", json=body)
    assert obs.download_calls == 1
    client.post("/api/files/uploads/stage", json=body)
    assert obs.download_calls == 1, "同名同大小应跳过重新下载"


# ── sha 校验 ────────────────────────────────────────────────────

def test_sha_mismatch_rejected(client, obs):
    """key 是内容寻址的，不符就意味着以后按 sha 命中缓存会拿到**别的文件**的解析结果。"""
    r = _upload(client, sha="f" * 64)
    assert r.status_code == 400
    assert "sha256" in r.json()["detail"]
    assert obs.put_calls == 0, "校验失败不该写任何东西"


def test_sha_optional(client, obs):
    r = _upload(client, sha="")
    assert r.status_code == 200
    assert r.json()["sha256"] == PPTX_SHA


def test_empty_file_rejected(client):
    r = _upload(client, data=b"")
    assert r.status_code == 400


# ── 路径安全 ────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "../escape.pptx", "a/b.pptx", "..\\x.pptx", ".hidden",
])
def test_stage_rejects_bad_dest_name(client, obs, bad):
    """dest_name 直接拼到 uploads/ 下面，必须是单个文件名。"""
    blob = _upload(client).json()
    r = client.post("/api/files/uploads/stage", json={
        "tenant_id": "tenant-1", "session_id": "sess-abc123",
        "file_key": blob["file_key"], "dest_name": bad,
    })
    assert r.status_code == 400, f"{bad} 应被拒绝"


def test_stage_rejects_traversal_in_key(client, obs):
    r = client.post("/api/files/uploads/stage", json={
        "tenant_id": "tenant-1", "session_id": "sess-abc123",
        "file_key": "uploads/../../etc/passwd", "dest_name": "x.pptx",
    })
    assert r.status_code == 400


def test_download_rejects_non_upload_key(client, obs):
    """下载端点只能取 uploads/ 前缀下的对象 —— 不能拿它读产物或别的 bucket 路径。"""
    r = client.get("/api/files/uploads/blob", params={"file_key": "sessions/s1/secret.xlsx"})
    assert r.status_code == 400


# ── 失败与降级 ──────────────────────────────────────────────────

def test_stage_missing_blob_is_404(client, obs):
    r = client.post("/api/files/uploads/stage", json={
        "tenant_id": "tenant-1", "session_id": "sess-abc123",
        "file_key": "uploads/tenant-1/" + "0" * 64 + ".pptx",
        "dest_name": "aaaaaaaa-x.pptx",
    })
    assert r.status_code == 404


def test_stage_without_shared_storage_is_503(client, obs, tmp_path, monkeypatch):
    """没共享存储时明确失败，不能假装成功。

    落在本机的文件沙箱里看不到 —— "看起来成功了但沙箱里没有"比明确失败糟得多，
    因为注入文本会告诉模型文件在那儿，然后它 open() 失败。
    """
    blob = _upload(client).json()
    monkeypatch.setenv("NFS_ROOT", str(tmp_path / "nonexistent"))
    file_stager.get_nfs_settings.cache_clear()

    r = client.post("/api/files/uploads/stage", json={
        "tenant_id": "tenant-1", "session_id": "sess-abc123",
        "file_key": blob["file_key"], "dest_name": blob["staged_name"],
    })
    assert r.status_code == 503
    assert "共享存储" in r.json()["detail"]


def test_quota_exceeded_before_download(client, obs, nfs, monkeypatch):
    """超配额时**在下载之前**就拒绝，不落盘也不白下载。

    先落盘再回滚踩过两个坑：落盘时 chmod 成只读，回滚的 unlink 在 Windows 上失败，
    而那个 except OSError 会静默吞掉 —— 超限的文件留在 uploads/ 一直占着配额，下次
    判定时把别人也挡在外面。
    """
    # 先塞一个 1MB 的文件把 1MB 配额占满
    (nfs / "uploads" / "filler.bin").write_bytes(b"x" * (1024 * 1024))
    monkeypatch.setenv("NFS_WORKSPACE_QUOTA_MB", "1")
    file_stager.get_nfs_settings.cache_clear()
    from lea_tools.api import files as files_api
    files_api.get_nfs_settings.cache_clear()

    blob = _upload(client).json()
    before = obs.download_calls

    r = client.post("/api/files/uploads/stage", json={
        "tenant_id": "tenant-1", "session_id": "sess-abc123",
        "file_key": blob["file_key"], "dest_name": blob["staged_name"],
    })
    assert r.status_code == 507, r.text
    assert "配额" in r.json()["detail"]
    assert not (nfs / "uploads" / blob["staged_name"]).exists(), "不该落盘"
    assert obs.download_calls == before, "不该白下载一份马上要删的字节"
    # 临时文件也不能留下
    assert not any(p.name.startswith(".staging-") for p in (nfs / "uploads").iterdir())


def test_already_staged_file_bypasses_quota(client, obs, nfs, monkeypatch):
    """已经物化过的文件不受配额影响 —— 它已经占着那部分空间了。

    不豁免的话会话重建时会失败：文件明明在那儿，却因为"再加一次它的大小超了"而报错。
    """
    blob = _upload(client).json()
    body = {"tenant_id": "tenant-1", "session_id": "sess-abc123",
            "file_key": blob["file_key"], "dest_name": blob["staged_name"]}
    assert client.post("/api/files/uploads/stage", json=body).status_code == 200

    # 现在把配额收到比这个文件还小
    monkeypatch.setenv("NFS_WORKSPACE_QUOTA_MB", "1")
    (nfs / "uploads" / "filler.bin").write_bytes(b"x" * (1024 * 1024))
    file_stager.get_nfs_settings.cache_clear()
    from lea_tools.api import files as files_api
    files_api.get_nfs_settings.cache_clear()

    r = client.post("/api/files/uploads/stage", json=body)
    assert r.status_code == 200, "已存在的文件应直接返回，不重新判配额"


# ── 下载 ────────────────────────────────────────────────────────

def test_download_returns_original_bytes(client, obs):
    """走 OBS 而不是 NFS —— 会话归档后 NFS 那份清理掉了，blob 还在。"""
    blob = _upload(client).json()
    r = client.get("/api/files/uploads/blob",
                   params={"file_key": blob["file_key"], "filename": "课件.pptx"})
    assert r.status_code == 200
    assert r.content == PPTX_BYTES
    assert "attachment" in r.headers["content-disposition"]


def test_download_expired_blob_is_410(client, obs):
    """410 而不是 404：对象曾经存在、被 lifecycle 清了，前端可以显示"已过期"。"""
    r = client.get("/api/files/uploads/blob", params={
        "file_key": "uploads/tenant-1/" + "9" * 64 + ".pdf"})
    assert r.status_code == 410


# ── 非 ASCII 文件名全链路 ────────────────────────────────────────

def test_chinese_filename_survives_round_trip(client, obs, nfs):
    """中文名要能穿过 multipart → OBS metadata → 落盘 → 下载。

    这条链上有三处会坏：HTTP header 只允许 ASCII（metadata 要编码）、文件系统的字节
    编码、以及 Content-Disposition 的 filename*。挨个都验一遍比事后查"文件名乱码"便宜。
    """
    name = "第三章 卷积神经网络（修订版）.pptx"
    blob = _upload(client, name=name).json()
    assert "第三章" in blob["staged_name"]

    r2 = client.post("/api/files/uploads/stage", json={
        "tenant_id": "tenant-1", "session_id": "sess-abc123",
        "file_key": blob["file_key"], "dest_name": blob["staged_name"],
    })
    assert r2.status_code == 200

    on_disk = nfs / "uploads" / blob["staged_name"]
    assert on_disk.is_file(), [p.name for p in (nfs / "uploads").iterdir()]

    r3 = client.get("/api/files/uploads/blob",
                    params={"file_key": blob["file_key"], "filename": name})
    assert r3.status_code == 200
    assert r3.content == PPTX_BYTES
    # RFC 5987 编码，浏览器才能还原中文名
    assert "filename*=UTF-8''" in r3.headers["content-disposition"]


def test_read_staged_text_file_through_mount(client, obs, nfs):
    """模型用 /files/read 读上传的文本文件 —— 走 /mnt/uploads 前缀。

    这条验的是 read_file 对两个 RO 挂载点都跳过 stat：stat 走严格的 _resolve，
    挂载点在那里必然越界。不跳的话会白跑一次注定失败的调用（异常被吞掉，所以
    症状是静默的性能损失，不是报错）。
    """
    text = "# 讲义\n第一节 导论".encode()
    blob = _upload(client, name="讲义.md", data=text).json()
    client.post("/api/files/uploads/stage", json={
        "tenant_id": "tenant-1", "session_id": "sess-abc123",
        "file_key": blob["file_key"], "dest_name": blob["staged_name"],
    })

    r = client.get("/api/files/read", params={
        "tenant_id": "tenant-1", "session_id": "sess-abc123",
        "path": f"{UPLOADS_MOUNT}/{blob['staged_name']}",
    })
    assert r.status_code == 200, r.text
    assert r.json()["content"] == "# 讲义\n第一节 导论"
    assert r.json()["stat"] is None, "只读挂载点不做新鲜度校验"


def test_write_to_uploads_mount_rejected(client, obs, nfs):
    """原件只读 —— 写进去会被拒。

    模型改完必须写 /workspace：那里才会被扫描推 S3 进文件抽屉，而且原件保住了才能重来。
    """
    blob = _upload(client, name="讲义.md", data=b"x").json()
    client.post("/api/files/uploads/stage", json={
        "tenant_id": "tenant-1", "session_id": "sess-abc123",
        "file_key": blob["file_key"], "dest_name": blob["staged_name"],
    })

    r = client.post("/api/files/write", json={
        "tenant_id": "tenant-1", "session_id": "sess-abc123",
        "path": f"{UPLOADS_MOUNT}/{blob['staged_name']}",
        "content": "篡改", "create_only": False,
    })
    assert r.status_code == 403, r.text


def test_unknown_extension_accepted(client, obs, nfs):
    """.zip 之类抽不出文本的格式也要能落地 —— 上传侧放宽 415 的意义就在这里。"""
    data = b"PK\x03\x04zip content"
    blob = _upload(client, name="作业包.zip", data=data).json()
    assert blob["file_key"].endswith(".zip")

    r = client.post("/api/files/uploads/stage", json={
        "tenant_id": "tenant-1", "session_id": "sess-abc123",
        "file_key": blob["file_key"], "dest_name": blob["staged_name"],
    })
    assert r.status_code == 200
    assert (nfs / "uploads" / blob["staged_name"]).read_bytes() == data
