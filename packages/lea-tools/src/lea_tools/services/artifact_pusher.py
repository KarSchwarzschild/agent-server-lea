"""ArtifactPusher — 把沙箱里生成的产物 push 到 S3。

方案 B 的关键组件：不挂 volume、run_code 后 lea-tools 从沙箱 read_file 拿到
产物字节 → boto3 put_object 到 S3。产物 outlive 沙箱、无 s3fs 语义问题。

Bucket 布局：<bucket>/sessions/<session_id>/<rel_path>
下载端点（api/files.py）从相同路径读，两边命名规则要同步。
"""

from __future__ import annotations

import boto3
import structlog
from botocore.client import Config as BotoConfig

from lea_tools.config import get_s3_settings

logger = structlog.get_logger()


def artifact_key(session_id: str, rel_path: str) -> str:
    """产物在 S3 里的 key。跟 api/files.py 下载端点的路径构造保持一致。

    形如：sessions/<sid>/<rel_path>。session_id 由 lea-agent 生成（UUID/雪花），
    我们相信它是 URL-safe 的（下载端点侧还会再做一次防御性校验）。
    """
    rel = rel_path.lstrip("/").replace("\\", "/")
    return f"sessions/{session_id}/{rel}"


class ArtifactPusher:
    """boto3 client 复用，避免每次 push 都建连接。

    使用：
        pusher = ArtifactPusher()
        pusher.upload(session_id, "report.xlsx", data_bytes)
    """

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

    def upload(self, session_id: str, rel_path: str, data: bytes,
               source: str = "generated") -> str:
        """PUT 一个产物对象。返回 S3 key。

        source: "generated"（沙箱执行产物）| "upload"（用户上传的附件）
        写到对象 metadata，list_session 时透传给前端用于分组展示。
        """
        self._ensure_client()
        key = artifact_key(session_id, rel_path)
        # 猜 content-type，下载时 head_object 能返给浏览器
        import mimetypes
        ctype = mimetypes.guess_type(rel_path)[0] or "application/octet-stream"
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=ctype,
            Metadata={"source": source},
        )
        logger.info("artifact_pushed",
                    session_id=session_id, key=key,
                    size=len(data), content_type=ctype, source=source)
        return key

    def list_session(self, session_id: str) -> list[dict]:
        """列出 session 下所有对象。返回 [{rel_path, size, last_modified}, ...]。

        source 需要额外 head_object 才能拿（list_objects_v2 不返 metadata），
        本方法先不 head，由调用方按需再查。
        """
        self._ensure_client()
        prefix = f"sessions/{session_id}/"
        out: list[dict] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                rel = obj["Key"][len(prefix):]
                if not rel:
                    continue
                out.append({
                    "rel_path": rel,
                    "size": obj["Size"],
                    "last_modified": obj["LastModified"],
                    "key": obj["Key"],
                })
        return out

    def download(self, session_id: str, rel_path: str) -> bytes:
        """GET 单个对象的字节。用于 session 恢复（S3 → 沙箱 /workspace）。"""
        self._ensure_client()
        key = artifact_key(session_id, rel_path)
        obj = self._client.get_object(Bucket=self._bucket, Key=key)
        return obj["Body"].read()
