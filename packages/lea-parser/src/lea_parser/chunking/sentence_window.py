"""句子窗口分块 — 按中文句子边界切分，附加上下文窗口。"""

from __future__ import annotations

import re


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？])", text)
    sentences: list[str] = []
    for part in parts:
        stripped = part.strip()
        if not stripped:
            continue
        if stripped in "。！？" and sentences:
            sentences[-1] += stripped
        else:
            sentences.append(stripped)
    return sentences


def _join_blocks(texts: list[str]) -> str:
    if not texts:
        return ""
    result = texts[0]
    for t in texts[1:]:
        if t and t[0] in "。！？；，、）」》】：":
            result += t
        else:
            result += "\n" + t
    return result


def chunk_sentence_window(
    text_blocks: list[dict],
    chunk_size: int = 200,
    window_size: int = 1,
) -> list[dict]:
    """按句子边界分块，每个 chunk 带 window_content 上下文。"""
    if not text_blocks:
        return []

    pages: dict[int, list[str]] = {}
    for block in text_blocks:
        pid = block.get("page_idx", 0)
        pages.setdefault(pid, []).append(block["text"])

    all_small: list[dict] = []
    for page_idx in sorted(pages.keys()):
        page_text = _join_blocks(pages[page_idx])
        sentences = _split_sentences(page_text)
        current = ""
        for sentence in sentences:
            if len(sentence) > chunk_size:
                if current:
                    all_small.append({"content": current, "page_idx": page_idx})
                    current = ""
                all_small.append({"content": sentence, "page_idx": page_idx})
                continue
            candidate = (current + "\n" + sentence).strip() if current else sentence
            if len(candidate) <= chunk_size:
                current = candidate
            else:
                if current:
                    all_small.append({"content": current, "page_idx": page_idx})
                current = sentence
        if current.strip():
            all_small.append({"content": current, "page_idx": page_idx})

    all_small = [c for c in all_small if len(c["content"].strip()) >= 2]

    chunks: list[dict] = []
    for i, chunk in enumerate(all_small):
        start = max(0, i - window_size)
        end = min(len(all_small), i + window_size + 1)
        window = "\n\n".join(all_small[j]["content"] for j in range(start, end))
        chunks.append({
            "content": chunk["content"],
            "window_content": window,
            "page": chunk["page_idx"] + 1,
            "type": "text",
        })
    return chunks
