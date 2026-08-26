try:
    from lea_rag.embeddings.bge_embedding import BGEEmbedding
except ImportError:
    BGEEmbedding = None

__all__ = ["BGEEmbedding"]
