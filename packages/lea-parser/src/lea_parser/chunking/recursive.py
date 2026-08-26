"""RecursiveCharacterTextSplitter 分块策略。"""

from __future__ import annotations

from langchain_text_splitters import RecursiveCharacterTextSplitter


def chunk_recursive(
    text: str,
    chunk_size: int = 200,
    chunk_overlap: int = 30,
) -> list[dict]:
    """langchain 递归分块 + sentence window 上下文。"""
    if chunk_overlap >= chunk_size:
        chunk_overlap = max(0, chunk_size // 4)
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],
    )
    docs = splitter.split_text(text)
    chunks: list[dict] = []
    for i, content in enumerate(docs):
        start = max(0, i - 1)
        end = min(len(docs), i + 2)
        window = "\n\n".join(docs[start:end])
        chunks.append({
            "content": content,
            "window_content": window,
            "page": 0,
            "type": "markdown",
        })
    return chunks
