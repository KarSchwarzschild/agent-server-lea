"""HybridRetrieverV2 的 Milvus 双路融合契约测试。"""

from __future__ import annotations

from types import SimpleNamespace

from lea_rag.retrieval.hybrid_v2 import HybridRetrieverV2
from pymilvus import RRFRanker, WeightedRanker


class FakeMilvusClient:
    def __init__(self):
        self.calls = []

    def hybrid_search(self, **kwargs):
        self.calls.append(kwargs)
        return [[{
            "entity": {
                "content": "result",
                "kb_id": 1,
                "document_id": 2,
                "section_id": 3,
                "tenant_id": "tenant-1",
                "chunk_type": "text",
                "source_filename": "doc.txt",
                "source_url": "",
                "page": 4,
            },
            "distance": 0.42,
        }]]

    def search(self, **kwargs):
        self.calls.append({"single_search": kwargs})
        return [[{
            "entity": {"content": "dense", "kb_id": 1},
            "distance": 0.8,
        }]]


def _retriever(rank_config: dict) -> tuple[HybridRetrieverV2, FakeMilvusClient]:
    client = FakeMilvusClient()
    retriever = object.__new__(HybridRetrieverV2)
    retriever._index = SimpleNamespace(collection_name="kb", client=client)
    settings = dict(
        hybrid_ranker="rrf",
        hybrid_dense_weight=0.5,
        hybrid_bm25_weight=0.5,
        hybrid_rrf_k=60,
    )
    settings.update(rank_config)
    retriever._settings = SimpleNamespace(**settings)
    return retriever, client


def test_hybrid_uses_dense_and_bm25_in_one_rpc():
    retriever, client = _retriever({})

    hits = retriever._hybrid_search("机器学习", [0.1, 0.2], "kb_id in [1]", 5)

    assert len(hits) == 1
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["collection_name"] == "kb"
    assert isinstance(call["ranker"], RRFRanker)
    assert len(call["reqs"]) == 2
    assert [req.anns_field for req in call["reqs"]] == ["dense_vector", "sparse_vector"]
    assert call["reqs"][0].data == [[0.1, 0.2]]
    assert call["reqs"][1].data == ["机器学习"]
    assert call["reqs"][0].filter == "kb_id in [1]"


def test_hybrid_can_use_weighted_ranker_for_ab_comparison():
    retriever, client = _retriever({
        "hybrid_ranker": "weighted",
        "hybrid_dense_weight": 0.7,
        "hybrid_bm25_weight": 0.3,
    })

    retriever._hybrid_search("query", [0.1], "", 3)

    ranker = client.calls[0]["ranker"]
    assert isinstance(ranker, WeightedRanker)
    assert ranker._weights == [0.7, 0.3]


def test_unknown_ranker_falls_back_to_rrf():
    retriever, client = _retriever({"hybrid_ranker": "not-a-ranker"})

    retriever._hybrid_search("query", [0.1], "", 3)

    assert isinstance(client.calls[0]["ranker"], RRFRanker)


def test_dense_mode_uses_single_dense_search():
    retriever, client = _retriever({})

    hits = retriever._dense_search([0.1], "kb_id in [1]", 3)

    assert hits[0].content == "dense"
    assert len(client.calls) == 1
    call = client.calls[0]["single_search"]
    assert call["anns_field"] == "dense_vector"
    assert call["data"] == [[0.1]]
