"""Milvus 知识库索引 v2 — pymilvus 直接操作，Jina dense + BM25 双字段。

替代旧版 LlamaIndex MilvusVectorStore 黑盒方案。
Collection schema: dense_vector (FLOAT_VECTOR 1024d) + content (VARCHAR with BM25)
"""

from __future__ import annotations

from typing import Any

import structlog
from pymilvus import (
    CollectionSchema,
    DataType,
    FieldSchema,
    Function,
    FunctionType,
    MilvusClient,
)

from lea_rag.config import MilvusSettings

logger = structlog.get_logger()


def _build_schema(dim: int = 1024) -> CollectionSchema:
    fields = [
        FieldSchema("id", DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(
            "content", DataType.VARCHAR, max_length=65535,
            enable_analyzer=True,
            analyzer_params={"type": "chinese"},
        ),
        FieldSchema("dense_vector", DataType.FLOAT_VECTOR, dim=dim),
        FieldSchema("sparse_vector", DataType.SPARSE_FLOAT_VECTOR),
        FieldSchema("kb_id", DataType.INT64),
        FieldSchema("document_id", DataType.INT64),
        FieldSchema("section_id", DataType.INT64),
        FieldSchema("tenant_id", DataType.VARCHAR, max_length=64),
        FieldSchema("chunk_type", DataType.VARCHAR, max_length=20),
        FieldSchema("source_filename", DataType.VARCHAR, max_length=300),
        FieldSchema("source_url", DataType.VARCHAR, max_length=500),
        FieldSchema("page", DataType.INT32),
    ]

    schema = CollectionSchema(fields, description="LEA Knowledge Base v2")
    schema.add_function(Function(
        name="bm25_fn",
        input_field_names=["content"],
        output_field_names=["sparse_vector"],
        function_type=FunctionType.BM25,
    ))
    return schema


class MilvusKBIndex:
    """知识库向量索引 v2 — pymilvus 直接操作。"""

    def __init__(self, settings: MilvusSettings | None = None):
        self._settings = settings or MilvusSettings()
        self._client = MilvusClient(uri=self._settings.uri, token=self._settings.token or "")
        self._collection = self._settings.default_collection
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        if self._client.has_collection(self._collection):
            return

        schema = _build_schema(dim=self._settings.dense_dim)

        self._client.create_collection(
            collection_name=self._collection,
            schema=schema,
        )

        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name="dense_vector",
            index_type="IVF_FLAT",
            metric_type="COSINE",
            params={"nlist": 128},
        )
        index_params.add_index(
            field_name="sparse_vector",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="BM25",
        )
        self._client.create_index(
            collection_name=self._collection,
            index_params=index_params,
        )
        self._client.load_collection(self._collection)
        logger.info("milvus_collection_created", collection=self._collection)

    def insert(self, rows: list[dict[str, Any]]) -> list[int]:
        """插入一批向量记录。

        每条 row 需要: content, dense_vector, kb_id, document_id,
        section_id, tenant_id, chunk_type, source_filename, source_url, page
        """
        if not rows:
            return []
        result = self._client.insert(
            collection_name=self._collection,
            data=rows,
        )
        ids = result.get("ids", []) if isinstance(result, dict) else getattr(result, "primary_keys", [])
        logger.info("milvus_inserted", collection=self._collection, count=len(rows))
        return ids

    def delete_by_document(self, document_id: int) -> None:
        self._client.delete(
            collection_name=self._collection,
            filter=f"document_id == {document_id}",
        )
        logger.info("milvus_deleted_by_doc", document_id=document_id)

    def delete_by_kb(self, kb_id: int) -> None:
        self._client.delete(
            collection_name=self._collection,
            filter=f"kb_id == {kb_id}",
        )
        logger.info("milvus_deleted_by_kb", kb_id=kb_id)

    @property
    def client(self) -> MilvusClient:
        return self._client

    @property
    def collection_name(self) -> str:
        return self._collection
