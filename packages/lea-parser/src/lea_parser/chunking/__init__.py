"""Chunking 包 — 分块策略。"""

from lea_parser.chunking.recursive import chunk_recursive
from lea_parser.chunking.sentence_window import chunk_sentence_window

__all__ = ["chunk_sentence_window", "chunk_recursive"]
