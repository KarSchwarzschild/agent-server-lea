"""RAG 引擎配置。"""

from __future__ import annotations

from pydantic_settings import BaseSettings


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

    model_config = {"env_prefix": "LEA_RAG_"}
