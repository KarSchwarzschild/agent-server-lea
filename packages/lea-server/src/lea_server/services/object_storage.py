"""S3 兼容对象存储 — MinIO / AWS S3 / 阿里 OSS。"""

from __future__ import annotations

import structlog

from lea_server.config import get_settings

logger = structlog.get_logger()


class ObjectStorage:

    def __init__(self):
        import boto3
        from botocore.config import Config

        settings = get_settings()
        self._bucket = settings.minio_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.minio_endpoint,
            aws_access_key_id=settings.minio_access_key,
            aws_secret_access_key=settings.minio_secret_key,
            config=Config(signature_version="s3v4"),
            region_name="us-east-1",
        )
        self._ensure_bucket()

    def _ensure_bucket(self):
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except self._client.exceptions.ClientError:
            self._client.create_bucket(Bucket=self._bucket)
            logger.info("bucket_created", bucket=self._bucket)

    def put_object(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)
        logger.info("object_uploaded", bucket=self._bucket, key=key, size=len(data))
        return key

    def get_object(self, key: str) -> bytes:
        resp = self._client.get_object(Bucket=self._bucket, Key=key)
        return resp["Body"].read()

    def delete_object(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)
        logger.info("object_deleted", bucket=self._bucket, key=key)

    def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )


_instance: ObjectStorage | None = None


def get_object_storage() -> ObjectStorage:
    global _instance
    if _instance is None:
        _instance = ObjectStorage()
    return _instance
