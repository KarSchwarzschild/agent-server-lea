"""SlideEditorPipeline — 对话式编辑已有演示文稿。

用户在对话中说 "第3页加个要点" / "封面标题改成xxx" / "删掉最后一页"，
Pipeline 解析意图 → 加载当前 slides → 执行修改 → 保存。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import structlog

from lea_graph.skills.pipeline import SkillEvent, SkillPipeline
from lea_graph.skills.pipelines.slide_prompts import (
    EDIT_SLIDE_PROMPT,
    LAYOUTS,
    PARSE_EDIT_INTENT_PROMPT,
    SYSTEM_PROMPT,
    build_slide_prompt,
    extract_svg,
    validate_svg,
)

logger = structlog.get_logger(__name__)


class SlideEditorPipeline(SkillPipeline):
    """对话式幻灯片编辑 Pipeline。"""

    async def run(
        self,
        definition,
        message: str,
        *,
        tools=None,
        llm_factory=None,
        **kwargs: Any,
    ) -> AsyncIterator[SkillEvent]:
        if llm_factory is None:
            from lea_graph.llm import get_chat_model
            llm_factory = lambda purpose="chat": get_chat_model(
                purpose=purpose, streaming=False, max_tokens=4096, timeout=300.0,
            )

        session_id = kwargs.get("session_id", "")
        artifact_id = kwargs.get("artifact_id", "")

        yield SkillEvent("progress", {"content": "正在理解修改意图..."})
        artifact = await self._load_artifact(session_id, artifact_id)
        if artifact is None:
            yield SkillEvent("error", {"message": "未找到当前演示文稿，请先生成一份"})
            return

        slides = artifact["slides"]
        outline = artifact.get("outline", [])

        intent = await self._parse_edit_intent(message, slides, outline, llm_factory)
        action = intent.get("action", "edit")
        page = intent.get("page", 1)
        instruction = intent.get("instruction", message)

        if action == "edit":
            async for evt in self._handle_edit(slides, outline, page, instruction, llm_factory):
                yield evt
        elif action == "add":
            async for evt in self._handle_add(slides, outline, page, instruction, llm_factory):
                yield evt
        elif action == "delete":
            async for evt in self._handle_delete(slides, outline, page):
                yield evt
        else:
            yield SkillEvent("error", {"message": f"未知操作: {action}"})
            return

        artifact["slides"] = slides
        artifact["outline"] = outline
        await self._save_artifact(artifact, session_id)
        yield SkillEvent("result", {
            "type": "slides_updated",
            "action": action,
            "page": page,
            "total_pages": len(slides),
            "slides": slides,
        })

    async def _handle_edit(
        self, slides: list, outline: list, page: int, instruction: str, llm_factory,
    ) -> AsyncIterator[SkillEvent]:
        """修改指定页。"""
        idx = self._resolve_page_index(page, len(slides))
        yield SkillEvent("progress", {"content": f"正在修改第 {idx + 1} 页..."})

        current_svg = slides[idx]
        new_svg = await self._edit_slide(current_svg, instruction, llm_factory)
        slides[idx] = new_svg
        yield SkillEvent("content", {
            "type": "slide_updated",
            "index": idx,
            "svg": new_svg,
        })

    async def _handle_add(
        self, slides: list, outline: list, page: int, instruction: str, llm_factory,
    ) -> AsyncIterator[SkillEvent]:
        """在指定位置插入新页。"""
        idx = min(page - 1, len(slides))
        yield SkillEvent("progress", {"content": f"正在生成新页并插入到第 {idx + 1} 位..."})

        llm = llm_factory("chat")
        user_prompt = build_slide_prompt("key_points", instruction, [instruction])
        resp = await llm.ainvoke([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ])
        new_svg = extract_svg(resp.content)
        slides.insert(idx, new_svg)
        outline.insert(idx, {"title": instruction, "layout": "key_points", "points": [instruction]})
        yield SkillEvent("content", {
            "type": "slide_added",
            "index": idx,
            "svg": new_svg,
            "total_pages": len(slides),
        })

    async def _handle_delete(
        self, slides: list, outline: list, page: int,
    ) -> AsyncIterator[SkillEvent]:
        """删除指定页。"""
        idx = self._resolve_page_index(page, len(slides))
        slides.pop(idx)
        if idx < len(outline):
            outline.pop(idx)
        yield SkillEvent("content", {
            "type": "slide_deleted",
            "index": idx,
            "total_pages": len(slides),
        })

    async def _parse_edit_intent(
        self, message: str, slides: list, outline: list, llm_factory,
    ) -> dict:
        """LLM 解析自然语言 → 结构化编辑指令。"""
        llm = llm_factory("routing")
        total = len(slides)
        summary = "\n".join(
            f"  第{i+1}页: {o.get('title', '?')}" for i, o in enumerate(outline)
        ) if outline else f"  共 {total} 页（无大纲信息）"

        prompt = PARSE_EDIT_INTENT_PROMPT.format(
            total_pages=total, outline_summary=summary, message=message,
        )
        resp = await llm.ainvoke([{"role": "user", "content": prompt}])
        text = resp.content.strip()

        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning("edit_intent_parse_failed", raw=text[:200])
            return {"action": "edit", "page": 1, "instruction": message}

    async def _edit_slide(self, current_svg: str, instruction: str, llm_factory) -> str:
        """基于当前 SVG + 修改指令生成新 SVG。"""
        llm = llm_factory("chat")
        prompt = EDIT_SLIDE_PROMPT.format(current_svg=current_svg, instruction=instruction)
        resp = await llm.ainvoke([{"role": "user", "content": prompt}])
        svg = extract_svg(resp.content)
        valid, _ = validate_svg(svg)
        if not valid:
            return current_svg
        return svg

    async def _load_artifact(self, session_id: str, artifact_id: str) -> dict | None:
        """从 MinIO 加载 slides 状态。"""
        from lea_server.services.object_storage import get_object_storage

        key = f"artifacts/{session_id}/{artifact_id}.json"
        try:
            data = get_object_storage().get_object(key)
            return json.loads(data)
        except Exception:
            logger.warning("artifact_load_failed", key=key)
            return None

    async def _save_artifact(self, artifact: dict, session_id: str) -> None:
        """保存更新后的 slides 状态。"""
        from lea_server.services.object_storage import get_object_storage

        artifact_id = artifact["artifact_id"]
        key = f"artifacts/{session_id}/{artifact_id}.json"
        data = json.dumps(artifact, ensure_ascii=False).encode()
        get_object_storage().put_object(key, data, "application/json")

    @staticmethod
    def _resolve_page_index(page: int, total: int) -> int:
        """处理页码：支持负数（-1 = 最后一页）。"""
        if page < 0:
            return max(0, total + page)
        return min(page - 1, total - 1)
