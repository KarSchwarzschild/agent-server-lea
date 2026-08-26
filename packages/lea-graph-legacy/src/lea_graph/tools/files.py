"""公共Skill: 文件读写。"""

from __future__ import annotations

from langchain_core.tools import tool
from langgraph.config import get_stream_writer


@tool
async def write_file(filename: str, content: str) -> str:
    """将内容写入文件。用于保存Agent生成的报告、综述等长文档。"""
    from lea_graph.services.file_storage import get_file_storage

    writer = get_stream_writer()
    writer({"type": "action", "tool": "write-file", "input": {"filename": filename}})

    storage = get_file_storage()
    file_id = await storage.save(content=content, filename=filename)

    writer({"type": "observation", "tool": "write-file", "status": "success", "file_id": file_id})
    return f"文件已保存: {filename} (ID: {file_id})"


@tool
async def read_file(file_id: str) -> str:
    """读取之前保存的文件内容。"""
    from lea_graph.services.file_storage import get_file_storage

    storage = get_file_storage()
    result = await storage.get(file_id)
    if result is None:
        return f"文件不存在: {file_id}"
    return result["content"]
