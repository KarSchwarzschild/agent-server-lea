"""RAG 引擎配置。"""

from __future__ import annotations

from pydantic_settings import BaseSettings


class MilvusSettings(BaseSettings):
    uri: str = "http://localhost:19530"
    token: str = ""
    default_collection: str = "lea_documents"
    dense_dim: int = 1024

    model_config = {"env_prefix": "LEA_MILVUS_"}


class EmbeddingSettings(BaseSettings):
    model: str = "bge-m3"
    api_base: str = "http://localhost:11434/v1"
    api_key: str = "EMPTY"
    dimension: int = 1024

    model_config = {"env_prefix": "LEA_EMBEDDING_"}


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

    model_config = {"env_prefix": "LEA_RAG_"}
