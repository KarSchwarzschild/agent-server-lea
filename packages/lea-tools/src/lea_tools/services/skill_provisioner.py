"""Skill 交付 — S3 真相 → NFS 缓存 → session 目录。

    S3 (bundle_uri)  ──下载解压一次──▶  {root}/cache/skills/<sha>/
                                              │  按 agent 绑定 copytree
                                              ▼
                                     {root}/tenants/<tid>/sessions/<sid>/skills/<name>/
                                              │  RO host-mount
                                              ▼
                                          沙箱 /opt/skills/<name>/

为什么 S3 是真相而不是 NFS 上摆一份：bundle_uri 的 key 形如
`skills/<tenant>/<name>/<sha256>.zip` —— **sha 在路径里，包因此不可变**。
缓存目录以 sha 命名，一次解压永久有效，不需要任何失效判断。skill 改了就是
新 sha、新 key、新缓存目录。NFS 单一目录没有版本标识，只能靠 mtime 比对或
手动清缓存，那是长期出 bug 的地方。

为什么 cache 目录不直接挂给沙箱：那等于共享挂载，任意 session 都能 `ls` 到
平台全部 skill（含没绑定的）。所以要按 agent 绑定拷一份到 session 目录，
未绑定的 skill 在沙箱里根本不存在。拷贝成本换隔离。
"""

from __future__ import annotations

import io
import os
import re
import shutil
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import structlog

from lea_tools.config import get_nfs_settings

logger = structlog.get_logger()

# skill name 用作目录名，且会拼进沙箱路径给 LLM 看 —— 严格限制字符集。
_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
# sha256 十六进制；也容忍上传侧未来换算法后的其它 hex 长度。
_SHA_RE = re.compile(r"^[0-9a-f]{8,128}$")

_client = None


def _s3_client():
    """复用 boto3 client。Skill 包使用 OBS 的 S3 兼容接口。

    注意不能用 lea-tools 自己的 S3_* —— 那套指向 lea-sandbox-volumes bucket，
    skill 包在 lea-uploads 里，是两个不同 bucket（甚至可能不同实例）。
    bucket 一律从 bundle_uri 解析，不用任何默认值。
    """
    global _client
    if _client is not None:
        return _client
    import boto3
    from botocore.client import Config as BotoConfig

    endpoint = os.getenv("OBS_ENDPOINT") or os.getenv("SKILL_S3_ENDPOINT") or os.getenv("MINIO_ENDPOINT", "http://localhost:9000")
    _client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.getenv("OBS_ACCESS_KEY") or os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
        aws_secret_access_key=os.getenv("OBS_SECRET_KEY") or os.getenv("MINIO_SECRET_KEY", "minioadmin"),
        config=BotoConfig(
            signature_version="s3v4",
            s3={"addressing_style": os.getenv("OBS_ADDRESSING_STYLE", "path")},
        ),
        region_name=os.getenv("OBS_REGION", "cn-north-4"),
    )
    return _client


def parse_bundle_uri(uri: str) -> tuple[str, str]:
    """`s3://bucket/path/to.zip` → `("bucket", "path/to.zip")`。"""
    p = urlparse(uri)
    if p.scheme != "s3" or not p.netloc or not p.path.lstrip("/"):
        raise ValueError(f"unsupported bundle_uri: {uri!r}")
    return p.netloc, p.path.lstrip("/")


def sha_from_key(key: str) -> str:
    """从 key 尾部取 sha 当缓存目录名（`.../<sha>.zip` → `<sha>`）。

    不是 hex 就抛 —— 宁可这个 skill 走不通，也不能让任意字符串变成路径片段。
    """
    stem = key.rsplit("/", 1)[-1]
    if stem.endswith(".zip"):
        stem = stem[: -len(".zip")]
    if not _SHA_RE.fullmatch(stem):
        raise ValueError(f"cannot derive sha from key: {key!r}")
    return stem


def _safe_extract(zf: zipfile.ZipFile, dest: Path, limit_bytes: int) -> None:
    """解压并剥掉 zip 内的顶层目录（约定：zip 里是 `<name>/SKILL.md`）。

    两道防护：
    - zip slip —— 每个 entry 解析后必须仍落在 dest 内（挡 `../` 和绝对路径）
    - 解压炸弹 —— 按 file_size 累加预算，超了直接放弃这个 skill
    """
    total = 0
    for info in zf.infolist():
        if info.is_dir():
            continue
        total += info.file_size
        if total > limit_bytes:
            raise ValueError(f"bundle exceeds {limit_bytes} bytes uncompressed")

        # 剥顶层目录：pptx-generator/references/x.md → references/x.md
        raw = info.filename.replace("\\", "/")
        parts = Path(raw).parts
        if len(parts) < 2:
            continue  # 顶层裸文件（zip 根下的 README 之类），不是我们要的结构
        rel = Path(*parts[1:])
        # 逐段白名单。下面的 resolve 检查已能挡住穿越，但它依赖平台如何解析
        # 盘符/UNC —— Windows 会把 "evil/C:/x" 里的 "C:" 静默吞掉，结果虽然仍
        # 落在 dest 内却不是 zip 里声明的路径。宁可显式拒绝，不吃平台差异。
        if any(p in ("..", "") or ":" in p for p in rel.parts):
            raise ValueError(f"unsafe path in bundle: {info.filename!r}")

        target = (dest / rel).resolve()
        if not str(target).startswith(str(dest.resolve()) + os.sep):
            raise ValueError(f"unsafe path in bundle: {info.filename!r}")

        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)


def ensure_cached(bucket: str, key: str, sha: str) -> Path:
    """确保 `{root}/cache/skills/<sha>/` 存在且已解压，返回该路径。

    命中直接返回（sha 寻址，内容必然一致）。miss 时下载 → 解压到 `.tmp-<sha>`
    → 原子 rename。用临时目录是为了防并发：两个 session 同时冷启同一 skill 时，
    不会有人读到半解压状态。
    """
    cfg = get_nfs_settings()
    cache_root = Path(cfg.root) / "cache" / "skills"
    final = cache_root / sha
    if (final / "SKILL.md").is_file():
        return final

    cache_root.mkdir(parents=True, exist_ok=True)
    tmp = cache_root / f".tmp-{sha}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)

    try:
        raw = _s3_client().get_object(Bucket=bucket, Key=key)["Body"].read()
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            _safe_extract(zf, tmp, cfg.skills_quota_mb * 1024 * 1024)
        # 解压完必须有 SKILL.md，否则这个 bundle 对 LLM 毫无用处。
        #
        # 不校验的后果实测过一次：_safe_extract 约定 zip 里是 `<name>/SKILL.md` 并剥掉
        # 顶层目录，若打包时在 skill 目录**内部**执行 zip（根下直接是 SKILL.md），每个
        # entry 只有一段路径、全部走 `len(parts) < 2` 的 continue 分支 —— 解压出一个空
        # 目录，而这里直接返回，provision_session_skills 于是 copytree 一个空目录并
        # done.append(name)，**报告投递成功**。
        #
        # 更糟的是第二次调用：空的 cache 目录留在盘上，`(final / "SKILL.md").is_file()`
        # 不成立于是重新解压，rename 撞上已存在的目录抛 OSError，被上层 except 吞掉 ——
        # 同一个 bundle 第一次"成功"第二次失败，排查时极具误导性。
        if not (tmp / "SKILL.md").is_file():
            raise ValueError(
                f"bundle {key} 解压后没有 SKILL.md —— "
                "zip 里应是 `<name>/SKILL.md`（从 skill 目录的父目录打包），"
                "不是把 SKILL.md 放在 zip 根下"
            )
        # rename 目标已存在说明别人刚好赢了竞争 —— 用他的，扔掉自己这份
        try:
            tmp.rename(final)
        except OSError:
            if not (final / "SKILL.md").is_file():
                raise
            shutil.rmtree(tmp, ignore_errors=True)
        logger.info("skill bundle cached", sha=sha, key=key, bytes=len(raw))
        return final
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def provision_session_skills(
    tenant_id: str, session_id: str, skills: list[dict]
) -> list[str]:
    """把 agent 绑定的 skill 投递到 session 的 skills 目录，返回成功的 name 列表。

    `skills` 元素形如 `{"name": "pptx-generator", "bundle_uri": "s3://..."}`。

    必须在建沙箱**之前**调用 —— Cube 的 host-mount 在沙箱创建那一刻生效，
    之后再往 hostPath 写东西，已起的沙箱看不到。

    每个 skill 独立 try：一个包坏了/S3 拉不到，其它照常投递。全失败也不抛，
    沙箱还是要起（只是 LLM 读不到 skill 文件）—— 让沙箱起不来是更糟的降级。
    """
    from lea_tools.services.nfs_provisioner import ensure_session_dir

    base = ensure_session_dir(tenant_id, session_id)
    if base is None:
        # 无共享存储（本机开发）：host-mount 本就不会请求，投递也没意义
        logger.debug("skip skill provision: no shared storage", session_id=session_id)
        return []

    dest_root = base / "skills"
    done: list[str] = []
    for s in skills:
        name = (s.get("name") or "").strip()
        uri = (s.get("bundle_uri") or "").strip()
        if not name or not uri:
            continue
        if not _NAME_RE.fullmatch(name):
            logger.warning("skill name rejected", name=name)
            continue
        try:
            bucket, key = parse_bundle_uri(uri)
            cached = ensure_cached(bucket, key, sha_from_key(key))
            dest = dest_root / name
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(cached, dest)
            done.append(name)
        except Exception as e:
            logger.warning("skill provision failed", skill=name, uri=uri, error=str(e))

    logger.info("session skills provisioned",
                session_id=session_id, count=len(done), skills=done)
    return done
