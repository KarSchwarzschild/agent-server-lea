"""SlideGeneratorPipeline — 首次生成 SVG 演示文稿。

流程: 用户主题 → LLM 大纲规划 → 逐页 SVG 生成 → MinIO 持久化。
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

import structlog

from lea_graph.skills.pipeline import SkillEvent, SkillPipeline
from lea_graph.skills.pipelines.slide_prompts import (
    OUTLINE_PROMPT,
    SYSTEM_PROMPT,
    build_slide_prompt,
    extract_svg,
    validate_svg,
)

logger = structlog.get_logger(__name__)

MAX_RETRIES = 2


class SlideGeneratorPipeline(SkillPipeline):
    """SVG 演示文稿生成 Pipeline。"""

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

        yield SkillEvent("progress", {"content": "正在规划演示文稿内容..."})
        messages = kwargs.get("messages")
        outline = await self._plan_outline(message, llm_factory, messages=messages)
        total = len(outline)
        yield SkillEvent("progress", {"content": f"大纲完成，共 {total} 页，正在并发生成..."})

        # 并发生成所有页的 SVG
        import asyncio
        tasks = [self._generate_slide_svg(page, llm_factory) for page in outline]
        slides = await asyncio.gather(*tasks)

        for i, svg in enumerate(slides):
            yield SkillEvent("content", {
                "type": "slide",
                "index": i,
                "svg": svg,
                "title": outline[i].get("title", ""),
            })

        artifact_id = await self._save_artifact(slides, outline, kwargs)
        yield SkillEvent("result", {
            "type": "slides_created",
            "artifact_id": artifact_id,
            "total_pages": total,
            "slides": slides,
        })

    async def _plan_outline(self, message: str, llm_factory, messages=None) -> list[dict]:
        """LLM 生成大纲 JSON。如果有对话历史，会作为上下文传入。"""
        llm = llm_factory("chat")
        prompt = OUTLINE_PROMPT.format(topic=message)

        # 构建 LLM 消息列表，带入对话上下文
        llm_messages = [{"role": "system", "content": "你是演示文稿内容规划专家。只输出 JSON。"}]
        if messages:
            from langchain_core.messages import AIMessage, HumanMessage
            # 取最近几轮对话作为上下文（避免太长）
            recent = messages[-10:] if len(messages) > 10 else messages
            for msg in recent:
                if isinstance(msg, HumanMessage):
                    llm_messages.append({"role": "user", "content": msg.content})
                elif isinstance(msg, AIMessage) and msg.content:
                    llm_messages.append({"role": "assistant", "content": msg.content[:500]})
        llm_messages.append({"role": "user", "content": prompt})

        resp = await llm.ainvoke(llm_messages)
        text = resp.content.strip()
        text = self._extract_json(text)
        try:
            outline = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("outline_parse_failed", raw=text[:200])
            outline = [
                {"title": "封面", "layout": "title_page", "points": [message]},
                {"title": "核心内容", "layout": "bento_grid", "points": ["要点1", "要点2", "要点3", "要点4"]},
                {"title": "总结", "layout": "closing", "points": ["谢谢"]},
            ]
        return outline

    async def _generate_slide_svg(self, page: dict, llm_factory) -> str:
        """生成单页 SVG，失败时重试。"""
        llm = llm_factory("chat")
        layout = page.get("layout", "bento_grid")
        title = page.get("title", "")
        points = page.get("points", [])
        user_prompt = build_slide_prompt(layout, title, points)

        for attempt in range(MAX_RETRIES + 1):
            resp = await llm.ainvoke([
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ])
            svg = extract_svg(resp.content)
            valid, err = validate_svg(svg)
            if valid:
                return svg
            logger.warning("svg_validation_failed", attempt=attempt, error=err)

        return svg  # 返回最后一次结果即使校验失败

    async def _save_artifact(
        self, slides: list[str], outline: list[dict], kwargs: dict
    ) -> str:
        """将 slides 状态持久化到 MinIO。"""
        from lea_server.services.object_storage import get_object_storage

        session_id = kwargs.get("session_id") or uuid.uuid4().hex
        # MinIO key 不允许特殊字符，清理 session_id
        safe_session_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", session_id)
        artifact_id = f"slides_{uuid.uuid4().hex[:12]}"
        artifact = {
            "artifact_id": artifact_id,
            "type": "slides",
            "slides": slides,
            "outline": outline,
            "session_id": session_id,
        }
        key = f"artifacts/{safe_session_id}/{artifact_id}.json"
        data = json.dumps(artifact, ensure_ascii=False).encode()
        get_object_storage().put_object(key, data, "application/json")
        logger.info("artifact_saved", key=key, pages=len(slides))
        return artifact_id

    @staticmethod
    def _extract_json(text: str) -> str:
        """从 LLM 输出中提取 JSON（处理 markdown 代码块）。"""
        if text.startswith("["):
            return text
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            return text[start : end + 1]
        return text
