"""FileStager — 把用户上传的原件从 OBS 物化到会话的 uploads/ 目录。

    上传                OBS  uploads/<tenant>/<sha256>.<ext>     ← 长期真源，不可变
      │                       │
      │                       │ 物化（本模块）
      │                       ▼
      └──抽文本──▶     NFS  sessions/<sid>/uploads/<sha8>-<原名>
                              │
                              │ RO host-mount
                              ▼
                        沙箱  /mnt/uploads/<sha8>-<原名>

## 为什么要"物化"这一步

沙箱里的 python 只认文件系统。`Presentation("/mnt/uploads/x.pptx")` 要求那个路径下
真的有个文件 —— OBS 上的对象不是路径，python-pptx 打不开它。所以必须把字节拉下来
落到那个会被挂进沙箱的目录里。

OBS 那份是**长期真源**（会话归档后还在，用户随时能下载）；NFS 那份是**为这个会话
摆出来的临时副本**（会话清理时删掉，需要时重新物化一次）。

## 为什么不复用 /files/write

那个端点的参数是 `content: str`（`.encode("utf-8")`），只能写文本 —— 二进制要先
base64，30MB 的文件还得在 lea-agent 和 lea-tools 之间再走一遍。这里让 lea-tools
自己从 OBS 拉，调用方只传一个 key。

顺带这也让两个大小上限各归各位：`_MAX_AGENT_FILE_MB=10` 管的是**模型自己写文件**
（读一个超大文件除了撑爆上下文没别的用），物化是另一条路，只受会话配额约束。
混成一个数的话，一个 20MB 的 PPT 能上传成功却在物化时 413 —— 用户看到的是
"传成功了但文件不见了"。

## 幂等

目标名是 `<sha8>-<原名>`，同内容同名。所以重复物化是覆盖同一份字节，结果一样；
已存在且大小对得上就直接跳过（不重新下载）。

⚠️ 文件名里保留**原始文件名**，不是只用 sha —— 这是 Assistants API 踩过的坑：它挂进
沙箱的名字是 file ID（`/mnt/data/file-p4zUlf6...`），原名从不下发，于是挂多个文件时
模型分不清哪个是哪个，只能靠额外注入一张对照表或者让模型自己建 symlink 补救。
路径里带原名，模型直接就能对上用户说的"那个课件"。
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import unicodedata
from pathlib import Path

import boto3
import structlog
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from lea_tools.config import get_nfs_settings, get_s3_settings

logger = structlog.get_logger()

# 上传原件在 OBS 里的 key 前缀。
UPLOAD_PREFIX = "uploads"

# 交付物在 OBS 里的 key 前缀。跟 UPLOAD_PREFIX **同一套寻址规则**
# （`<prefix>/<tenant>/<sha256>.<ext>`），只是分开一个前缀。
#
# ## 为什么不是 artifact_pusher 那个 `sessions/<sid>/<rel_path>`
#
# 那个形状把会话关系编进了 key。三个后果：
#
#  1. 跨会话导入要另写一条物化路径 —— /uploads/stage 只认内容寻址的 key，而它是
#     唯一验证过的"OBS → 会话 uploads/ → 沙箱"通路。按 session 分层的 key 进不去。
#  2. 同一份内容交付多次（改了两轮又回到原样）占多个对象。
#  3. 配额算出来的数字跟对象存储的真实占用不符。
#
# 前缀分开而不是干脆共用 `uploads/`，是因为两者的**来源**不同，而来源决定了两件事：
# 出问题时先查谁（用户传的 vs 我们生成的），以及能不能删（用户的原件只有他能删，
# 交付物删掉还能重新生成）。key 里带着这个区分，运维不用查库就知道在看什么。
LIBRARY_PREFIX = "library"

# 允许被物化进会话 uploads/ 的 key 前缀。
#
# ⚠️ 这是一道**安全边界**，不是便利性检查：/uploads/stage 拿到 key 就去 OBS 拉字节，
# 而那个 bucket 里还有沙箱的 volume 对象和别的租户的东西。不限前缀的话，一个能打到
# lea-tools 的调用方可以把 bucket 里任意对象物化进任意会话的 uploads/ 目录，然后用
# Read 读出来 —— 内部服务不做 JWT（见模块头），所以这里必须自己收口。
#
# 租户隔离由 key 的第二段（`<prefix>/<tenant>/...`）承担，见 _validate_blob_key。
STAGEABLE_PREFIXES = (UPLOAD_PREFIX, LIBRARY_PREFIX)


def blob_key(prefix: str, tenant_id: str, sha256: str, ext: str) -> str:
    """`<prefix>/<tenant_id>/<sha256>.<ext>` —— 上传件和交付物共用的寻址规则。"""
    ext = (ext or "").lower().lstrip(".")
    suffix = f".{ext}" if ext else ""
    return f"{prefix}/{tenant_id}/{sha256}{suffix}"


def library_key(tenant_id: str, sha256: str, ext: str) -> str:
    """交付物在 OBS 里的 key。见 LIBRARY_PREFIX 的注释。"""
    return blob_key(LIBRARY_PREFIX, tenant_id, sha256, ext)

# 文件名里不允许出现的字符。`/` 和 `\` 会造成路径穿越，控制字符会让日志和终端错乱，
# Windows 保留字符是为了让同一份 NFS 在混合环境下也能用。
_UNSAFE_CHARS = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]')
# 原名部分的长度上限。整个文件名要留给 sha 前缀（9 字节）和扩展名，而多数文件系统
# 的单段上限是 255 字节 —— 中文按 UTF-8 是 3 字节一个字，所以按字节算不按字符算。
_MAX_NAME_BYTES = 180


def upload_key(tenant_id: str, sha256: str, ext: str) -> str:
    """原件在 OBS 里的 key。

    **带 tenant 前缀**，不是全局按 sha 寻址。这样同租户内天然去重（一个班 40 个学生
    传同一份课件只占一个对象），但跨租户不共享。

    跨租户共享看起来更省，但它引入两个真问题：A 租户删掉文件会让 B 的消失；以及
    "上传后发现已存在"变成一个存在性探测（能问出别的租户有没有某个文件）。而**去重
    收益主要来自同一个班/同一门课**，那本来就在一个租户里 —— 所以放弃跨租户去重
    几乎不损失什么。

    ## ⚠️ 这里原来写着一句错的话

    原文是："另外这也让'永不显式删除'这个策略能成立：blob 只靠 OBS lifecycle 规则
    过期。"——**那条 lifecycle 规则从来没有配过**。infra/ 里搜不到任何 OBS 生命周期
    配置，所以真实状态是 blob 永不过期、只增不减。

    而且现在有了用户配额，"永不显式删除"本身就是错的策略：删一行登记不释放任何空间，
    用户在存储页看到已用量不降，只能继续删，而空间永远回不来 —— 那正是 ChatGPT 的
    文件库最被诟病的形状。所以现在有显式删除（DELETE /api/user/files/...，同时删对象
    和行），而**跨租户不去重恰好让它安全**：一个对象只属于一个租户，删它不会让别人的
    文件消失。同租户内的引用计数由调用方在库里查（同 sha 还有没有别的行），
    见 lea-agent 的 handleDeleteUserFile。
    """
    return blob_key(UPLOAD_PREFIX, tenant_id, sha256, ext)


def safe_stage_name(sha256: str, filename: str) -> str:
    """物化到 uploads/ 时用的文件名：`<sha8>-<清洗过的原名>`。

    ⚠️ 这是**下发给模型的路径**的一部分，所以清洗规则只能有一份实现 —— 结果存进
    chat_attachments.staged_path，别在任何别处重新拼。两处规则在边缘情况下不一致的
    表现是模型拿到一个不存在的路径，而报错会说"文件不存在"，看起来像物化失败。

    sha 前缀解决同名不同内容互相覆盖；保留原名让模型能对上用户的说法。
    """
    # NFKC 归一：全角字符和兼容字符折叠成常规形式，免得同一个名字在不同输入法下产生
    # 不同字节序列（用户看到一样，磁盘上是两个文件）。
    name = unicodedata.normalize("NFKC", filename or "")
    name = _UNSAFE_CHARS.sub("_", name).strip().strip(".")
    # 消掉纯 `.` / `..` 这种；上面 strip(".") 之后通常已经空了，兜一手。
    if name in ("", ".", ".."):
        name = "file"

    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    # 按字节截断，且不能把一个多字节字符切一半（会产生非法 UTF-8 文件名）。
    encoded = stem.encode("utf-8")[:_MAX_NAME_BYTES]
    stem = encoded.decode("utf-8", errors="ignore") or "file"

    tail = f".{ext}" if ext else ""
    return f"{sha256[:8]}-{stem}{tail}"


class QuotaExceeded(Exception):
    """会话文件配额不够放这个文件。带上三个数，调用方要拼成给用户看的提示。"""

    def __init__(self, used: int, incoming: int, limit: int):
        self.used = used
        self.incoming = incoming
        self.limit = limit
        super().__init__(
            f"quota exceeded: used={used} incoming={incoming} limit={limit}"
        )


class FileStager:
    """OBS ←→ 会话 uploads/ 目录。boto3 client 复用，跟 ArtifactPusher 同一个形状。"""

    def __init__(self):
        self._client = None
        self._bucket = ""

    def _ensure_client(self):
        if self._client is not None:
            return
        s = get_s3_settings()
        endpoint = s.internal
        if not endpoint or not s.access_key:
            raise RuntimeError("S3 not configured (S3_INTERNAL_ENDPOINT/S3_ACCESS_KEY)")
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=s.access_key,
            aws_secret_access_key=s.secret_key,
            region_name=s.region,
            config=BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": "path" if s.use_path_style else "virtual"},
            ),
        )
        self._bucket = s.bucket

    def put_blob(self, tenant_id: str, sha256: str, ext: str, data: bytes,
                 filename: str = "") -> str:
        """把**用户上传的原件**字节写进 OBS，返回 key。"""
        return self.put_object(
            upload_key(tenant_id, sha256, ext), data,
            filename=filename, source="upload",
        )

    def put_library_object(self, tenant_id: str, sha256: str, ext: str, data: bytes,
                           filename: str = "") -> str:
        """把**交付物**字节写进 OBS 的 library/ 前缀，返回 key。

        跟 put_blob 走同一段代码，只差 key 前缀和 metadata 里的 source。分成两个入口
        而不是让调用方传前缀，是为了让"谁在往哪写"在调用点就读得出来 —— 前缀是个
        安全相关的量（STAGEABLE_PREFIXES 认它），不该是个可以随手传错的字符串参数。
        """
        return self.put_object(
            library_key(tenant_id, sha256, ext), data,
            filename=filename, source="artifact",
        )

    def put_object(self, key: str, data: bytes, filename: str = "",
                   source: str = "upload") -> str:
        """写一个内容寻址的对象，返回 key。已存在就跳过（同 key 必同内容）。

        原始文件名进 metadata 而不是 key：key 要保持内容寻址（同内容同 key），而同一份
        内容可能被不同人以不同文件名上传、或被模型以不同名字交付。metadata 只用于排查
        和下载时的 filename，不参与任何判定。

        source 是 "upload" 或 "artifact"，只做排查线索 —— 判定用 key 前缀（那个查得
        动、列得出来），不用 metadata（list_objects 不返回它，要逐个 head_object）。
        """
        self._ensure_client()

        try:
            head = self._client.head_object(Bucket=self._bucket, Key=key)
            if head.get("ContentLength") == len(data):
                logger.info("blob_exists", key=key, size=len(data), source=source)
                return key
            # 大小不一致 = sha 撞了（实际不可能）或者上一次写了半个对象。覆盖它。
            logger.warning("blob_size_mismatch", key=key,
                           existing=head.get("ContentLength"), incoming=len(data))
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code not in ("404", "NoSuchKey", "NotFound"):
                raise

        import mimetypes
        ctype = mimetypes.guess_type(filename or key)[0] or "application/octet-stream"
        # metadata 的值必须是 ASCII（HTTP header 约束），中文文件名要编码。用
        # RFC 2047 那套太重，这里直接 URL 编码 —— 只给人看和下载时还原，不参与判定。
        from urllib.parse import quote
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=ctype,
            Metadata={"source": source, "filename": quote(filename or "")},
        )
        logger.info("blob_put", key=key, size=len(data), content_type=ctype, source=source)
        return key

    def delete_object(self, key: str) -> None:
        """删掉一个对象。用户在文件库里删文件走这条路。

        ## 为什么必须真删对象，不能只删库里那一行

        有配额之后"只删登记"是错的：用户删了十个文件，存储页的已用量一动不动，
        而唯一的解释是"我们其实没删"。空间永远回不来，只增不减 —— 而 OBS 那边**没有
        任何 lifecycle 规则**（infra/ 里搜不到，见 upload_key 的注释），所以没有任何
        兜底会替我们回收。

        引用计数在调用方做（同租户内可能有别的行指向同一个 sha），这里只负责删。

        S3 的 DELETE 对不存在的 key 也返回 204，所以这个方法天然幂等 —— 删一半失败后
        重试是安全的。
        """
        self._ensure_client()
        self._client.delete_object(Bucket=self._bucket, Key=key)
        logger.info("blob_deleted", key=key)

    def stage(self, tenant_id: str, session_id: str, file_key: str,
              dest_name: str, quota_bytes: int = 0) -> tuple[str, int]:
        """把 OBS 上的对象物化到会话 uploads/ 目录。返回 (文件名, 字节数)。

        目标目录不存在时**不创建**：那说明 lea-tools 跟 Cube 节点没挂同一份存储
        （本机开发，或 lea-tools 独立部署），host-mount 由 cubelet 在节点侧建。
        这时物化没有意义 —— 落在本机的文件沙箱里看不到，而"看起来成功了但沙箱里没有"
        比明确失败糟得多。

        ## 配额在**下载之前**判，不是落盘之后回滚

        先落盘再回滚有两个毛病，都踩过：

        1. 落盘时把文件 chmod 成 0o444（只读），于是回滚的 unlink 在 Windows 上失败，
           而那个 `except OSError: pass` 会把失败静默吞掉 —— 超限的文件留在 uploads/
           里一直占着配额，下次判定时把别人也挡在外面。
        2. 白下载一份马上要删的字节。

        head_object 本来就要打一次（拿 ContentLength 做幂等判断），所以提前判是**零
        额外成本**的。
        """
        cfg = get_nfs_settings()
        uploads_dir = Path(cfg.root) / "tenants" / tenant_id / "sessions" / session_id / "uploads"

        # 会话目录不存在时创建它（第一次物化时必然不存在）。只在共享存储根本不可达时
        # 才报错 —— 那时创建会成功但沙箱读不到，所以必须拦住。
        if not Path(cfg.root).is_dir():
            raise RuntimeError(
                f"共享存储不可达（{cfg.root}）—— lea-tools 与沙箱节点未共享存储，"
                f"物化的文件沙箱里读不到"
            )
        uploads_dir.mkdir(parents=True, exist_ok=True)

        target = uploads_dir / dest_name
        self._ensure_client()

        try:
            head = self._client.head_object(Bucket=self._bucket, Key=file_key)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                raise FileNotFoundError(f"OBS 上没有这个对象：{file_key}")
            raise
        expected = head.get("ContentLength", 0)

        # 已经在了就跳过下载。内容寻址（名字里带 sha）保证同名同内容，所以只比大小
        # 就够 —— 不需要重算 sha。也不判配额：它已经占着那部分空间了。
        if target.exists() and target.stat().st_size == expected:
            logger.info("stage_skip_existing", session_id=session_id,
                        dest=dest_name, size=expected)
            return dest_name, expected

        if quota_bytes > 0:
            used = self.uploads_bytes(tenant_id, session_id)
            if used + expected > quota_bytes:
                raise QuotaExceeded(used, expected, quota_bytes)

        # 先写临时文件再 os.replace —— 跟 NFSFileStore.write 同一个理由：POSIX 保证
        # rename 原子，所以并发的读者（沙箱里正在跑的 python）要么看到旧的完整版、
        # 要么新的完整版，不会读到半个文件。
        #
        # 直接 download_file 到目标路径的话，沙箱有真实概率打开一个只下载了一半的
        # pptx，而 python-pptx 报的是"文件损坏"—— 那个错误信息完全指不到真因。
        fd, tmp_path = tempfile.mkstemp(dir=str(uploads_dir), prefix=".staging-")
        os.close(fd)
        try:
            with open(tmp_path, "wb") as f:
                self._client.download_fileobj(self._bucket, file_key, f)
            size = os.path.getsize(tmp_path)
            # 只读：这个目录 RO 挂进沙箱，但宿主侧的 mode 也一并收紧 —— 双保险，而且
            # 让"谁把原件改了"这个问题在宿主侧也答得上。
            os.chmod(tmp_path, 0o444)
            os.replace(tmp_path, target)
        except Exception:
            # 失败时别把临时文件留在 uploads 里 —— 它会被挂进沙箱，模型 ls 到一个
            # `.staging-xxx` 会困惑，而且占着配额。
            #
            # chmod 之后可能已经是只读了（Windows 上 unlink 只读文件会 EACCES），
            # 所以先把写位加回来再删。
            try:
                os.chmod(tmp_path, 0o644)
            except OSError:
                pass
            try:
                os.unlink(tmp_path)
            except OSError:
                logger.warning("staging_tmp_leaked", path=tmp_path)
            raise

        logger.info("staged", session_id=session_id, key=file_key,
                    dest=dest_name, size=size)
        return dest_name, size

    def uploads_bytes(self, tenant_id: str, session_id: str) -> int:
        """会话 uploads/ 目录的当前占用。配额判定用。

        **现算而不是维护计数器**：计数器在多节点下要处理并发递增、删除对账、以及
        "进程挂在递增和落盘之间"的漂移，而漂移的表现是配额莫名其妙变紧、只能手动清。
        物化是低频操作（一次上传一次），遍历一个目录便宜得多，而且零漂移。
        """
        cfg = get_nfs_settings()
        uploads_dir = Path(cfg.root) / "tenants" / tenant_id / "sessions" / session_id / "uploads"
        if not uploads_dir.is_dir():
            return 0
        total = 0
        for root, _dirs, files in os.walk(uploads_dir):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    continue
        return total

    def fetch_blob(self, file_key: str) -> tuple[bytes, str]:
        """取原件字节 + content-type。用户下载走这条路（不经 NFS）。

        走 OBS 而不是 NFS，因为 blob 是真源：会话归档后 NFS 目录已经删了，blob 还在。
        """
        self._ensure_client()
        try:
            obj = self._client.get_object(Bucket=self._bucket, Key=file_key)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                raise FileNotFoundError(f"OBS 上没有这个对象：{file_key}")
            raise
        ctype = obj.get("ContentType") or "application/octet-stream"
        return obj["Body"].read(), ctype


def prune_session_uploads(tenant_id: str, session_id: str) -> int:
    """删掉会话的 uploads/ 目录，返回释放的字节数。会话清理时调。

    原件在 OBS 里还有一份，所以这里删掉是安全的 —— 需要时重新物化。
    """
    cfg = get_nfs_settings()
    uploads_dir = Path(cfg.root) / "tenants" / tenant_id / "sessions" / session_id / "uploads"
    if not uploads_dir.is_dir():
        return 0
    freed = 0
    for root, _dirs, files in os.walk(uploads_dir):
        for name in files:
            try:
                freed += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    shutil.rmtree(uploads_dir, ignore_errors=True)
    logger.info("uploads_pruned", session_id=session_id, freed=freed)
    return freed
