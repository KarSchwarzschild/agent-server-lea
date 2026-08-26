"""SkillBundleStore — Skill bundle 的对象存储 + 本地缓存。

设计：
- 内容寻址：bundle 对象 key = ``skills/{sha256}.tar.gz``。同 hash → 同对象，push 幂等。
- 二级缓存：``~/.lea/skill-cache/{sha256}/`` 存解压后的 bundle。SkillLoader 读文件系统。
- 确定性打包：tarfile 手工填 header (mtime=0/uid=0/gid=0/mode=0644 目录 0755)，
  按路径排序，保证同内容 → 同 sha256。
- 原子写：解压到 ``{sha256}.partial`` → 完整后 rename → 触发 SkillLoader 判定 "OK"。

用法（服务端 seed / admin 上传）：
    store = SkillBundleStore.from_env()
    bundle = store.pack(Path("packages/lea-graph/src/lea_graph/skills/builtin/data-analyst"))
    uri = store.push(bundle)  # s3://lea-uploads/skills/{sha256}.tar.gz
    # 存 DB: bundle_uri=uri, bundle_hash=bundle.sha256, bundle_size=bundle.size

用法（运行时 pull）：
    local_dir = store.pull(bundle_uri, bundle_hash)  # 命中则秒回，miss 则拉+校验+解压
    # SkillLoader 走原本的文件系统扫描
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import structlog

logger = structlog.get_logger()


BUNDLE_KEY_PREFIX = "skills"
CACHE_ROOT_ENV = "LEA_SKILL_CACHE_DIR"
CACHE_OK_MARKER = ".ok"           # 存在即表示解压完整
CACHE_MANIFEST_FILE = ".manifest"  # 可选：便于外部审计
BUNDLE_MAX_BYTES = 50 * 1024 * 1024  # 50MB，防单包爆盘


class BundleError(Exception):
    """SkillBundle IO / integrity 异常基类。"""


class BundleIntegrityError(BundleError):
    """下载的 tarball hash 与预期不符。"""


@dataclass
class SkillBundle:
    """打包结果 —— 内存里的 tarball + 元信息。"""

    tarball: bytes = b""
    sha256: str = ""
    size: int = 0
    manifest: dict[str, str] = field(default_factory=dict)  # {rel_path: file_sha256}
    top_level: str = ""  # bundle 根目录名（skill_dir.name）

    @property
    def object_key(self) -> str:
        return f"{BUNDLE_KEY_PREFIX}/{self.sha256}.tar.gz"


class SkillBundleStore:
    """MinIO / S3 上的 skill bundle 存储 + 本地解压缓存。"""

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        cache_root: Path | None = None,
        region_name: str = "us-east-1",
    ):
        import boto3
        from botocore.config import Config

        self._bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4"),
            region_name=region_name,
        )
        self._cache = Path(cache_root or _default_cache_root()).resolve()
        self._cache.mkdir(parents=True, exist_ok=True)
        self._ensure_bucket()

    @classmethod
    def from_env(cls) -> SkillBundleStore:
        """从环境变量构造（对齐 lea_server settings）。"""
        return cls(
            bucket=os.getenv("MINIO_BUCKET", "lea-uploads"),
            endpoint_url=os.getenv("MINIO_ENDPOINT", "http://localhost:9000"),
            access_key=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
            secret_key=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
        )

    # ── pack ────────────────────────────────────────────────────
    def pack(self, src_dir: Path) -> SkillBundle:
        """确定性打包 src_dir → tar.gz bytes + sha256 + manifest。

        src_dir 必须包含 skill.md；打进 tar 的路径都相对 src_dir.name。
        例如 src_dir = ".../builtin/data-analyst"，tar 内路径为
        "data-analyst/skill.md"、"data-analyst/references/notes.md" 等。
        """
        src_dir = Path(src_dir).resolve()
        if not src_dir.is_dir():
            raise BundleError(f"bundle src_dir 不存在或非目录: {src_dir}")
        if not (src_dir / "skill.md").exists():
            raise BundleError(f"bundle 根目录缺少 skill.md: {src_dir}")

        top = src_dir.name
        files = _collect_files(src_dir)
        if not files:
            raise BundleError(f"bundle 为空: {src_dir}")

        manifest: dict[str, str] = {}
        buf = io.BytesIO()
        # gzip mtime=0 → 同内容同 hash
        with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT, compresslevel=6) as tf:
            _add_deterministic(tf, src_dir, top, name=top, is_dir=True)
            for rel in files:  # 已排序
                abs_path = src_dir / rel
                data = abs_path.read_bytes()
                arcname = f"{top}/{rel.replace(os.sep, '/')}"
                _add_deterministic(tf, abs_path, arcname, data=data)
                manifest[rel.replace(os.sep, "/")] = hashlib.sha256(data).hexdigest()

        # 强制 gzip header 无时间戳 —— tarfile mode="w:gz" 默认写 mtime
        tarball = _strip_gzip_mtime(buf.getvalue())
        sha256 = hashlib.sha256(tarball).hexdigest()
        size = len(tarball)
        if size > BUNDLE_MAX_BYTES:
            raise BundleError(f"bundle 超出上限 {size} > {BUNDLE_MAX_BYTES}")

        logger.info(
            "skill_bundle_packed",
            top=top,
            files=len(manifest),
            sha256=sha256,
            size=size,
        )
        return SkillBundle(
            tarball=tarball,
            sha256=sha256,
            size=size,
            manifest=manifest,
            top_level=top,
        )

    # ── push ────────────────────────────────────────────────────
    def push(self, bundle: SkillBundle) -> str:
        """幂等上传。返回 bundle_uri: s3://{bucket}/{key}"""
        key = bundle.object_key
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            logger.info("skill_bundle_push_skip", sha256=bundle.sha256, reason="exists")
        except self._client.exceptions.ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code not in ("404", "NoSuchKey", "NotFound"):
                raise
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=bundle.tarball,
                ContentType="application/gzip",
                Metadata={"sha256": bundle.sha256, "top-level": bundle.top_level},
            )
            logger.info(
                "skill_bundle_pushed",
                sha256=bundle.sha256,
                key=key,
                size=bundle.size,
            )
        return f"s3://{self._bucket}/{key}"

    # ── pull ────────────────────────────────────────────────────
    def pull(self, bundle_uri: str, expected_hash: str) -> Path:
        """返回本地 cache 目录。命中即秒回；miss 则拉+校验+原子解压。

        返回目录里是解压后的 bundle 顶层目录（例如 ``{cache}/{hash}/data-analyst/``
        的父目录 ``{cache}/{hash}/``）。SkillLoader 直接对返回的路径 rglob("skill.md")。
        """
        if not expected_hash:
            raise BundleError("pull 需要 expected_hash")
        cache_dir = self._cache / expected_hash
        if (cache_dir / CACHE_OK_MARKER).exists():
            return cache_dir

        key = _uri_to_key(bundle_uri, self._bucket)
        logger.info("skill_bundle_pull_start", sha256=expected_hash, key=key)
        tarball = self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()

        actual = hashlib.sha256(tarball).hexdigest()
        if actual != expected_hash:
            raise BundleIntegrityError(
                f"bundle sha256 mismatch: expected={expected_hash} actual={actual} uri={bundle_uri}"
            )

        # 原子解压：解到 .partial 再 rename，防止半解压 cache 污染
        partial = self._cache / f"{expected_hash}.partial"
        if partial.exists():
            shutil.rmtree(partial, ignore_errors=True)
        partial.mkdir(parents=True, exist_ok=True)
        try:
            with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tf:
                _safe_extract(tf, partial)
            (partial / CACHE_OK_MARKER).write_text(expected_hash, encoding="utf-8")
            # 已有旧的（可能是半解压残留）先清掉
            if cache_dir.exists():
                shutil.rmtree(cache_dir, ignore_errors=True)
            partial.rename(cache_dir)
        except Exception:
            shutil.rmtree(partial, ignore_errors=True)
            raise

        logger.info("skill_bundle_pulled", sha256=expected_hash, cache=str(cache_dir))
        return cache_dir

    # ── cache 淘汰 ──────────────────────────────────────────────
    def evict_cache(self, keep_hashes: Iterable[str]) -> int:
        """删除本地 cache 里 hash 不在 keep_hashes 中的目录。

        典型调用点：SkillLoader 从 DB 拉完所有 published skill 的 bundle_hash 后，
        调用一次让 cache 收敛。返回被删的目录数。

        TODO(user): 见下方 _should_evict —— 淘汰策略你来定。
        """
        keep = set(keep_hashes)
        removed = 0
        for entry in self._cache.iterdir():
            if not entry.is_dir():
                continue
            if entry.name.endswith(".partial"):
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
                continue
            if len(entry.name) != 64 or not all(c in "0123456789abcdef" for c in entry.name):
                continue  # 不是我们的 hash 目录，别碰
            if _should_evict(entry, keep):
                shutil.rmtree(entry, ignore_errors=True)
                logger.info("skill_bundle_cache_evicted", sha256=entry.name)
                removed += 1
        return removed

    # ── infra ───────────────────────────────────────────────────
    def _ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except self._client.exceptions.ClientError:
            self._client.create_bucket(Bucket=self._bucket)
            logger.info("skill_bundle_bucket_created", bucket=self._bucket)


# ── 淘汰策略 · 用户填 ──────────────────────────────────────────────
def _should_evict(cache_dir: Path, keep_hashes: set[str]) -> bool:
    """决定 cache_dir 是否该被淘汰。

    上下文：
      - cache_dir.name 是 sha256（64 位 hex）
      - keep_hashes 是 DB 里当前所有 published skill 的 bundle_hash 集合
      - 一个 skill 换版本会往这里堆新目录，旧 hash 不再有引用但仍占盘

    可考虑的策略（选一个，或组合）：
      A. 严格：不在 keep_hashes 里就删（干净但历史版本 rollback 时要重下）
      B. 保护最近 N 天 access：os.path.getatime 判断
      C. 保护到磁盘阈值：cache 目录总大小超过 X GB 才踢老的
      D. 结合 keep_hashes + 一个 SKILL_CACHE_KEEP_UNPINNED_HOURS env 兜底

    TODO(user): 填 5-10 行判定逻辑。
    默认（占位）：严格模式 —— 不在 keep_hashes 就删。
    """
    return cache_dir.name not in keep_hashes


# ── helpers ────────────────────────────────────────────────────────
def _default_cache_root() -> Path:
    override = os.getenv(CACHE_ROOT_ENV)
    if override:
        return Path(override)
    return Path.home() / ".lea" / "skill-cache"


def _collect_files(root: Path) -> list[str]:
    """返回 root 下所有文件的相对路径（sorted，用 posix 分隔符）。"""
    out: list[str] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        # 忽略常见垃圾
        if any(seg.startswith(".") for seg in rel.split("/")):
            continue
        if rel.endswith((".pyc",)) or "__pycache__" in rel.split("/"):
            continue
        out.append(rel)
    out.sort()
    return out


def _add_deterministic(
    tf: tarfile.TarFile,
    src: Path,
    arcname: str,
    *,
    is_dir: bool = False,
    name: str | None = None,
    data: bytes | None = None,
) -> None:
    """写入 tarball 的一个 entry —— 清空所有非确定性字段。"""
    ti = tarfile.TarInfo(name=name or arcname)
    ti.mtime = 0
    ti.uid = 0
    ti.gid = 0
    ti.uname = ""
    ti.gname = ""
    if is_dir:
        ti.type = tarfile.DIRTYPE
        ti.mode = 0o755
        tf.addfile(ti)
    else:
        assert data is not None
        ti.type = tarfile.REGTYPE
        ti.mode = 0o644
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))


def _strip_gzip_mtime(gz: bytes) -> bytes:
    """把 gzip header 里的 mtime 4 字节清零。

    gzip 结构：\x1f\x8b + method(1) + flags(1) + mtime(4) + xflags(1) + os(1) + ...
    tarfile mode="w:gz" 会写入当时的 mtime，导致同内容不同 hash。
    """
    if len(gz) < 10 or gz[:2] != b"\x1f\x8b":
        return gz
    return gz[:4] + b"\x00\x00\x00\x00" + gz[8:]


def _safe_extract(tf: tarfile.TarFile, dest: Path) -> None:
    """防 tar path traversal —— 拒绝任何路径逃出 dest 的成员。"""
    dest_resolved = dest.resolve()
    for member in tf.getmembers():
        target = (dest / member.name).resolve()
        if not str(target).startswith(str(dest_resolved)):
            raise BundleError(f"tar path traversal 拒绝: {member.name}")
        if member.islnk() or member.issym():
            raise BundleError(f"tar 不允许 hardlink/symlink: {member.name}")
    tf.extractall(dest)


def _uri_to_key(bundle_uri: str, expected_bucket: str) -> str:
    """s3://bucket/prefix/xxx.tar.gz → prefix/xxx.tar.gz"""
    if not bundle_uri.startswith("s3://"):
        raise BundleError(f"不支持的 bundle_uri（应以 s3:// 开头）: {bundle_uri}")
    rest = bundle_uri[5:]
    slash = rest.find("/")
    if slash < 0:
        raise BundleError(f"bundle_uri 缺 key: {bundle_uri}")
    bucket, key = rest[:slash], rest[slash + 1:]
    if bucket != expected_bucket:
        logger.warning(
            "skill_bundle_uri_bucket_mismatch",
            uri_bucket=bucket,
            client_bucket=expected_bucket,
            hint="继续用 client 的 bucket 读取；旧 URI 建议 admin 页面里 rewrite",
        )
    return key
