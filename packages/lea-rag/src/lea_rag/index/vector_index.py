"""Milvus 向量索引 — 稠密 + 稀疏混合检索。"""

from __future__ import annotations

import structlog
from llama_index.core import StorageContext, VectorStoreIndex
from llama_index.core.schema import Document
from llama_index.vector_stores.milvus import MilvusVectorStore

from lea_rag.config import EmbeddingSettings, MilvusSettings

logger = structlog.get_logger(__name__)

_embed_initialized = False


def _init_embed_model() -> None:
    """配置 LlamaIndex 全局 embedding model（仅执行一次）。

    优先级：
    1. BGE-M3 自部署服务（/embed 接口）
    2. OpenAI 兼容 API（Ollama/vLLM）
    3. HuggingFace 本地模型（下载到本地跑）
    """
    global _embed_initialized
    if _embed_initialized:
        return
    _embed_initialized = True

    from llama_index.core import Settings as LISettings

    embed_cfg = EmbeddingSettings()

    # 1. 尝试 BGE-M3 自部署服务
    try:
        import httpx
        resp = httpx.get(f"{embed_cfg.api_base}/health", timeout=3)
        if resp.status_code == 200:
            from lea_rag.embeddings.bge_embedding import BGEEmbedding
            LISettings.embed_model = BGEEmbedding(api_base=embed_cfg.api_base)
            logger.info("embed_model_bge_service", api_base=embed_cfg.api_base)
            return
    except Exception:
        pass

    # 2. 尝试 OpenAI 兼容 API
    try:
        import httpx
        resp = httpx.get(f"{embed_cfg.api_base}/models", timeout=3)
        if resp.status_code == 200:
            from llama_index.embeddings.openai import OpenAIEmbedding
            LISettings.embed_model = OpenAIEmbedding(
                model_name=embed_cfg.model,
                api_base=embed_cfg.api_base,
                api_key=embed_cfg.api_key,
                dimensions=embed_cfg.dimension,
            )
            logger.info("embed_model_openai", model=embed_cfg.model, api_base=embed_cfg.api_base)
            return
    except Exception:
        pass

    # 3. Fallback: 无可用 embedding 服务，跳过向量索引
    logger.warning("embed_model_unavailable", msg="No embedding service available. Vector indexing will be skipped.")


class MilvusRAGIndex:
    """基于 LlamaIndex MilvusVectorStore 的向量索引。

    支持 BGE-M3 稠密 + 稀疏双向量，通过 RRF 融合排序。
    """

    def __init__(
        self,
        collection_name: str | None = None,
        settings: MilvusSettings | None = None,
        overwrite: bool = False,
    ):
        _init_embed_model()

        self._settings = settings or MilvusSettings()
        collection = collection_name or self._settings.default_collection

        dim = self._settings.dense_dim
        try:
            from llama_index.core import Settings as LISettings
            if hasattr(LISettings, "embed_model") and hasattr(LISettings.embed_model, "dimensions"):
                dim = LISettings.embed_model.dimensions
            elif hasattr(LISettings.embed_model, "_model") and hasattr(LISettings.embed_model._model, "get_sentence_embedding_dimension"):
                dim = LISettings.embed_model._model.get_sentence_embedding_dimension()
        except Exception:
            pass

        self.vector_store = MilvusVectorStore(
            uri=self._settings.uri,
            token=self._settings.token or None,
            collection_name=collection,
            dim=dim,
            enable_sparse=True,
            hybrid_ranker="RRFRanker",
            hybrid_ranker_params={"k": 60},
            overwrite=overwrite,
        )
        storage_ctx = StorageContext.from_defaults(vector_store=self.vector_store)
        self._index = VectorStoreIndex.from_vector_store(
            self.vector_store, storage_context=storage_ctx
        )
        logger.info("milvus_index_ready", collection=collection, uri=self._settings.uri)

    @property
    def index(self) -> VectorStoreIndex:
        return self._index

    def as_retriever(self, top_k: int = 10, **kwargs):
        return self._index.as_retriever(similarity_top_k=top_k, **kwargs)

    def as_query_engine(self, top_k: int = 5, **kwargs):
        return self._index.as_query_engine(similarity_top_k=top_k, **kwargs)

    def insert_documents(self, documents: list[Document]) -> None:
        for doc in documents:
            self._index.insert(doc)
        logger.info("documents_inserted", count=len(documents))

    def delete_document(self, doc_id: str) -> None:
        self._index.delete_ref_doc(doc_id)
        logger.info("document_deleted", doc_id=doc_id)

    def delete_by_metadata(self, key: str, value) -> None:
        """按 metadata 字段批量删除向量（如 source_id=123）。"""
        expr = f'{key} == {value}' if isinstance(value, (int, float)) else f'{key} == "{value}"'
        collection = self.vector_store.collection_name
        self.vector_store.client.delete(collection_name=collection, filter=expr)
        logger.info("deleted_by_metadata", collection=collection, filter=expr)
