"""记忆服务配置 — 预留独立 embedding 口，MVP 先 fallback 到 KB 的 BGE-M3。"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings


class MemorySettings(BaseSettings):
    """记忆服务配置，从 LEA_MEMORY_ 前缀环境变量读取。

    embedding 相关字段为空时 fallback 到 KB 的配置（LEA_EMBEDDING_* / LEA_MILVUS_*）。
    """

    embedding_api_base: str = ""
    embedding_model: str = ""
    embedding_dim: int = 0

    milvus_collection: str = "lea_user_memories"

    extract_interval: int = 5
    extract_min_messages: int = 6
    max_memories_per_user: int = 200
    dedup_threshold: float = 0.85

    model_config = {"env_prefix": "LEA_MEMORY_", "env_file": ".env", "extra": "ignore"}

    def get_effective_embedding_api_base(self) -> str:
        if self.embedding_api_base:
            return self.embedding_api_base
        import os
        return os.getenv("LEA_EMBEDDING_API_BASE", "http://localhost:8012")

    def get_effective_embedding_model(self) -> str:
        if self.embedding_model:
            return self.embedding_model
        import os
        return os.getenv("LEA_EMBEDDING_MODEL", "bge-m3")

    def get_effective_dim(self) -> int:
        if self.embedding_dim > 0:
            return self.embedding_dim
        import os
        return int(os.getenv("LEA_EMBEDDING_DIMENSION", "1024"))


@lru_cache
def get_memory_settings() -> MemorySettings:
    return MemorySettings()
