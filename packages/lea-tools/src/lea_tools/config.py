"""Tool Gateway 配置。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings

_env_loaded = False


def _ensure_env():
    global _env_loaded
    if not _env_loaded:
        root = Path(__file__).parent.parent.parent.parent.parent
        env_file = root / ".env"
        if env_file.exists():
            load_dotenv(env_file)
        _env_loaded = True


_ensure_env()


class DatabaseSettings(BaseSettings):
    host: str = Field(default="localhost", alias="DB_HOST")
    port: int = Field(default=3306, alias="DB_PORT")
    user: str = Field(default="root", alias="DB_USER")
    password: str = Field(default="", alias="DB_PASSWORD")
    name: str = Field(default="lea-platform-test", alias="DB_NAME")

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def url(self) -> str:
        if self.host in ("sqlite", ""):
            from pathlib import Path
            return f"sqlite:///{Path(self.name).with_suffix('.db')}"
        import urllib.parse
        pwd = urllib.parse.quote_plus(self.password)
        return f"mysql+pymysql://{self.user}:{pwd}@{self.host}:{self.port}/{self.name}?charset=utf8mb4"


class RedisSettings(BaseSettings):
    url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    model_config = {"env_file": ".env", "extra": "ignore"}


class EncryptionSettings(BaseSettings):
    key: str = Field(default="", alias="ENCRYPTION_KEY")

    model_config = {"env_file": ".env", "extra": "ignore"}


class InternalServiceSettings(BaseSettings):
    lea_rag_url: str = Field(default="http://localhost:8300", alias="LEA_RAG_URL")
    e2b_api_url: str = Field(default="http://localhost:3000", alias="E2B_API_URL")

    model_config = {"env_file": ".env", "extra": "ignore"}


class S3Settings(BaseSettings):
    """沙箱 volume 后端的 S3 兼容存储。跟 scripts/volume-s3.conf 同一份配置，
    只是从 .env 读；生产环境这两份要保持一致。

    Endpoint 分层：
    - endpoint: 兼容旧配置，等价于 internal_endpoint 的 fallback
    - internal_endpoint: lea-tools 拉数据用，生产走内网
      (http://minio.internal:9000)
    - public_endpoint: 未来给浏览器直下预签 URL 用；本版只加字段留口子

    property internal / public 会做 fallback：都空时回落到 endpoint。
    """

    endpoint: str = Field(default="", alias="S3_ENDPOINT")
    internal_endpoint: str = Field(default="", alias="S3_INTERNAL_ENDPOINT")
    public_endpoint: str = Field(default="", alias="S3_PUBLIC_ENDPOINT")
    bucket: str = Field(default="lea-sandbox-volumes", alias="S3_BUCKET")
    access_key: str = Field(default="", alias="S3_ACCESS_KEY")
    secret_key: str = Field(default="", alias="S3_SECRET_KEY")
    region: str = Field(default="us-east-1", alias="S3_REGION")
    use_path_style: bool = Field(default=True, alias="S3_USE_PATH_STYLE")

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def internal(self) -> str:
        """lea-tools 内部访问 S3 用的 endpoint。"""
        return self.internal_endpoint or self.endpoint

    @property
    def public(self) -> str:
        """浏览器直下预签 URL 用的 endpoint（未启用时回落到 internal）。"""
        return self.public_endpoint or self.internal


class NFSSettings(BaseSettings):
    """NFS 持久化存储配置。单节点时 root 就是本地 XFS 路径；
    多节点时 root 是 NFS 挂载点（所有节点一致）。"""

    root: str = Field(default="/data/cubelet/lea", alias="NFS_ROOT")
    workspace_quota_mb: int = Field(default=500, alias="NFS_WORKSPACE_QUOTA_MB")
    skills_quota_mb: int = Field(default=50, alias="NFS_SKILLS_QUOTA_MB")

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_database_settings() -> DatabaseSettings:
    return DatabaseSettings()


@lru_cache
def get_redis_settings() -> RedisSettings:
    return RedisSettings()


@lru_cache
def get_encryption_settings() -> EncryptionSettings:
    return EncryptionSettings()


@lru_cache
def get_internal_service_settings() -> InternalServiceSettings:
    return InternalServiceSettings()


@lru_cache
def get_s3_settings() -> S3Settings:
    return S3Settings()


@lru_cache
def get_nfs_settings() -> NFSSettings:
    return NFSSettings()
