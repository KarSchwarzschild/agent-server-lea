"""RAG 引擎配置。"""

from __future__ import annotations

import urllib.parse
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
            return f"sqlite:///{Path(self.name).with_suffix('.db')}"
        pwd = urllib.parse.quote_plus(self.password)
        return f"mysql+pymysql://{self.user}:{pwd}@{self.host}:{self.port}/{self.name}?charset=utf8mb4"


class StorageSettings(BaseSettings):
    endpoint: str = Field(default="http://localhost:9000", alias="MINIO_ENDPOINT")
    access_key: str = Field(default="minioadmin", alias="MINIO_ACCESS_KEY")
    secret_key: str = Field(default="minioadmin", alias="MINIO_SECRET_KEY")
    bucket: str = Field(default="lea-uploads", alias="MINIO_BUCKET")

    model_config = {"env_file": ".env", "extra": "ignore"}


class MilvusSettings(BaseSettings):
    uri: str = "http://localhost:19530"
    token: str = ""
    default_collection: str = "lea_knowledge_v2"
    dense_dim: int = 1536

    model_config = {"env_prefix": "LEA_MILVUS_"}


class EmbeddingSettings(BaseSettings):
    """Fallback 配置 — 仅当 retrieval_models 表为空时使用。"""
    model: str = "bge-large-zh-v1.5"
    api_base: str = "https://tenantapi-ai.ouchn.edu.cn/v1/embeddings"
    api_key: str = ""
    dimension: int = 1536

    model_config = {"env_prefix": "LEA_EMBEDDING_"}


def get_tenant_api_key() -> str:
    """获取中转平台 API key（embedding 和 LLM 共用）。"""
    import os
    return os.getenv("LEA_TENANT_API_KEY", "")


class RerankerSettings(BaseSettings):
    model: str = "bge-reranker-v2-m3"
    api_base: str = "http://localhost:8100"
    top_k: int = 5
    enabled: bool = True
    breaker_fail_max: int = 3
    breaker_reset_timeout: int = 60

    model_config = {"env_prefix": "LEA_RERANKER_"}


class AcademicSettings(BaseSettings):
    enabled: bool = True
    wanfang_enabled: bool = True
    wanfang_app_key: str = "ve58gaw86xt0"
    wanfang_app_secret: str = "911w15ec6mbfwxirq59kwbq9ztud49p9"
    arxiv_enabled: bool = True
    semantic_scholar_enabled: bool = True
    semantic_scholar_api_key: str = ""
    proxy: str = ""

    model_config = {"env_prefix": "LEA_ACADEMIC_"}


class RAGSettings(BaseSettings):
    chunk_size: int = 512
    chunk_overlap: int = 64
    retrieval_top_k: int = 10
    rerank_top_k: int = 5
    query_optimize: bool = False
    section_boost: bool = True
    # dense cosine 和 BM25 分数不在同一量纲，默认用 RRF 只按名次融合，避免
    # 某一路因为分值尺度更大而长期压制另一条路。weighted 仅用于 A/B 对照。
    hybrid_ranker: str = "rrf"
    hybrid_dense_weight: float = 0.5
    hybrid_bm25_weight: float = 0.5
    hybrid_rrf_k: int = 60
    # 图片 chunk 做 embedding 时额外带多少字周边正文。
    #
    # 为什么需要：图片单靠 VLM 描述检索不出来。一张柱状图的描述是"柱状图显示三个
    # 季度的数据对比"，用户问"第三季度营收多少"匹配不上 —— 描述里没有"营收"这个
    # 词，它在图片周围的正文里。带上同页正文之后这张图才可能被召回。
    #
    # 文本侧早就有这个机制（parser.py 的 window_content，前后各一个 chunk 一起
    # embed，content 仍存窄的那份），图片侧一直没有。这里补齐，口径一致。
    #
    # 按字数而不是 token 数：embedding 服务那边不暴露分词器，拿字数当代理指标
    # 够用，而且中文下字数和 token 数接近。0 表示不带上下文（退回原行为）。
    image_context_chars: int = 500

    model_config = {"env_prefix": "LEA_RAG_"}


@lru_cache
def get_database_settings() -> DatabaseSettings:
    return DatabaseSettings()


@lru_cache
def get_storage_settings() -> StorageSettings:
    return StorageSettings()
