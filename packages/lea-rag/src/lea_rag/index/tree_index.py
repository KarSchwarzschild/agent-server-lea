"""LlamaIndex TreeIndex — 树状摘要索引（替代老项目自研 PageIndex）。

适合长文档的层次化摘要和深度问答：leaf 节点存原始分块，
上层节点由 LLM 递归摘要生成，查询时 top-down 选择性遍历。
"""

from __future__ import annotations

import structlog
from llama_index.core import TreeIndex
from llama_index.core.schema import Document

logger = structlog.get_logger(__name__)


class TreeRAGIndex:
    """基于 LlamaIndex TreeIndex 的摘要索引。"""

    def __init__(self, documents: list[Document] | None = None):
        if documents:
            self._index = TreeIndex.from_documents(documents)
            logger.info("tree_index_built", doc_count=len(documents))
        else:
            self._index = TreeIndex([])

    @property
    def index(self) -> TreeIndex:
        return self._index

    def as_query_engine(self, **kwargs):
        return self._index.as_query_engine(**kwargs)

    def insert_documents(self, documents: list[Document]) -> None:
        for doc in documents:
            self._index.insert(doc)
        logger.info("tree_documents_inserted", count=len(documents))
