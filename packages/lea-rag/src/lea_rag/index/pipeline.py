"""Ingestion Pipeline — 文档分块 + 向量化 + 存储。"""

from __future__ import annotations

from pathlib import Path

import structlog
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import Document

from lea_rag.config import RAGSettings
from lea_rag.index.document_loader import load_file
from lea_rag.index.vector_index import MilvusRAGIndex

logger = structlog.get_logger(__name__)


class IngestionPipeline:
    """文件 → 分块 → 向量化 → 写入 Milvus。"""

    def __init__(
        self,
        index: MilvusRAGIndex,
        settings: RAGSettings | None = None,
    ):
        self._index = index
        s = settings or RAGSettings()
        self._splitter = SentenceSplitter(
            chunk_size=s.chunk_size,
            chunk_overlap=s.chunk_overlap,
        )

    def ingest_file(self, path: str | Path, *, metadata: dict | None = None) -> int:
        """解析文件 → 分块 → 写入索引，返回节点数。"""
        docs = load_file(path, metadata=metadata)
        return self.ingest_documents(docs)

    def ingest_documents(self, documents: list[Document]) -> int:
        nodes = self._splitter.get_nodes_from_documents(documents)
        self._index.insert_documents(
            [Document(text=n.get_content(), metadata=n.metadata) for n in nodes]
        )
        logger.info("ingestion_complete", documents=len(documents), nodes=len(nodes))
        return len(nodes)
