"""配置中心 — DB / Redis / MinIO / 外部服务。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings

load_dotenv(Path(__file__).parent.parent.parent.parent.parent / ".env")


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
            return f"sqlite:///{self.name}.db"
        import urllib.parse
        pwd = urllib.parse.quote_plus(self.password)
        return f"mysql+pymysql://{self.user}:{pwd}@{self.host}:{self.port}/{self.name}?charset=utf8mb4"


class RedisSettings(BaseSettings):
    host: str = Field(default="202.205.160.8", alias="REDIS_HOST")
    port: int = Field(default=16379, alias="REDIS_PORT")
    db: int = Field(default=15, alias="REDIS_DB")
    password: str = Field(default="", alias="REDIS_PASSWORD")
    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def url(self) -> str:
        if self.password:
            return f"redis://:{self.password}@{self.host}:{self.port}/{self.db}"
        return f"redis://{self.host}:{self.port}/{self.db}"


class StorageSettings(BaseSettings):
    endpoint: str = Field(default="http://localhost:9000", alias="MINIO_ENDPOINT")
    access_key: str = Field(default="minioadmin", alias="MINIO_ACCESS_KEY")
    secret_key: str = Field(default="minioadmin", alias="MINIO_SECRET_KEY")
    bucket: str = Field(default="lea-files", alias="MINIO_BUCKET")
    model_config = {"env_file": ".env", "extra": "ignore"}


class ServiceSettings(BaseSettings):
    """外部服务地址。"""
    mineru_url: str = Field(default="", alias="MINERU_URL")
    mineru_token: str = Field(default="", alias="MINERU_TOKEN")
    subtitle_service_url: str = Field(default="", alias="SUBTITLE_SERVICE_URL")
    asr_url: str = Field(default="", alias="ASR_URL")
    asr_token: str = Field(default="", alias="ASR_TOKEN")
    jwt_secret: str = Field(default="lea-secret-key", alias="JWT_SECRET")
    model_config = {"env_file": ".env", "extra": "ignore"}


class ParserSettings(BaseSettings):
    """解析参数默认值。"""
    default_chunk_size: int = 200
    default_chunk_overlap: int = 30
    max_file_size_mb: int = 200
    async_threshold_mb: int = 10
    model_config = {"env_file": ".env", "env_prefix": "PARSER_", "extra": "ignore"}


@lru_cache
def get_db_settings() -> DatabaseSettings:
    return DatabaseSettings()


@lru_cache
def get_redis_settings() -> RedisSettings:
    return RedisSettings()


@lru_cache
def get_storage_settings() -> StorageSettings:
    return StorageSettings()


@lru_cache
def get_service_settings() -> ServiceSettings:
    return ServiceSettings()


@lru_cache
def get_parser_settings() -> ParserSettings:
    return ParserSettings()
