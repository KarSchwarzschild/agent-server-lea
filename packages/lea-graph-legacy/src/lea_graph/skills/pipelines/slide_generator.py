"""SlideGeneratorPipeline — 生成 SlideData JSON 格式的演示文稿。

流程: 用户主题 → LLM 一次性产出完整 SlideData JSON → 通过 artifact_* 事件推给前端

输出格式对齐 frontend-user/src/components/super-agent/SlideRenderer.vue 的
6 种 legacy layout：title / content / two_column / image_left / comparison / blank
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
    SLIDE_JSON_SYSTEM_PROMPT,
    SLIDES_JSON_USER_PROMPT,
    extract_json_object,
    normalize_slide_data,
)

logger = structlog.get_logger(__name__)

MAX_RETRIES = 2


class SlideGeneratorPipeline(SkillPipeline):
    """演示文稿生成 Pipeline（JSON 模式）。"""

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
                purpose=purpose, streaming=False, max_tokens=8000, timeout=300.0,
            )

        artifact_id = f"slides_{uuid.uuid4().hex[:12]}"
        title_snippet = message.strip()[:40] or "演示文稿"

        yield SkillEvent("progress", {"content": "正在设计演示文稿..."})

        # 1) 骨架：面板/预览卡片立即弹出
        # 注意 seq 从 1 开始（前端 artifact store 的 lastSeq 初始 0，用 `<=` 做去重）
        yield SkillEvent("artifact_create", {
            "artifact_id": artifact_id,
            "title": title_snippet,
            "artifact_type": "slides",
            "doc_category": "presentation",
            "version_index": 1,
            "seq": 1,
        })

        # 2) LLM 一次性生成完整 JSON
        slides_data = await self._generate_slides_json(
            message, llm_factory, messages=kwargs.get("messages")
        )
        json_str = json.dumps(slides_data, ensure_ascii=False)
        total_pages = len(slides_data["slides"])
        deck_title = slides_data.get("metadata", {}).get("title") or title_snippet
        summary_text = (
            f"已为你生成《{deck_title}》共 {total_pages} 页演示文稿，"
            "点击右下角卡片查看和编辑。"
        )

        # 3) 聊天气泡文字（避免气泡为空 / DB 侧被 reload 覆盖丢失）
        yield SkillEvent("content", {"text": summary_text})

        # 4) delta 全推（B1 一次推整包）
        yield SkillEvent("artifact_delta", {
            "artifact_id": artifact_id,
            "delta": json_str,
            "seq": 2,
            "version_index": 1,
        })

        # 5) done
        yield SkillEvent("artifact_done", {
            "artifact_id": artifact_id,
            "content": json_str,
            "version_index": 1,
            "seq": 3,
        })

        # 6) 持久化（MinIO；DB artifact 双源问题留到 Phase C）
        try:
            await self._save_artifact(artifact_id, slides_data, kwargs)
        except Exception as e:
            logger.warning("slides_artifact_persist_failed", error=str(e))

        # 7) result 事件供 supervisor 落 session_metadata + AIMessage 内容
        yield SkillEvent("result", {
            "type": "slides_created",
            "artifact_id": artifact_id,
            "total_pages": total_pages,
            "summary_text": summary_text,
        })

    async def _generate_slides_json(
        self, message: str, llm_factory, messages=None
    ) -> dict:
        """调用 LLM 产出完整 SlideData JSON；失败时重试并归一。"""
        llm = llm_factory("chat")

        llm_messages: list[dict[str, str]] = [
            {"role": "system", "content": SLIDE_JSON_SYSTEM_PROMPT},
        ]
        if messages:
            from langchain_core.messages import AIMessage, HumanMessage
            recent = messages[-10:] if len(messages) > 10 else messages
            for msg in recent:
                if isinstance(msg, HumanMessage):
                    llm_messages.append({"role": "user", "content": msg.content})
                elif isinstance(msg, AIMessage) and msg.content:
                    llm_messages.append(
                        {"role": "assistant", "content": msg.content[:500]}
                    )
        llm_messages.append(
            {
                "role": "user",
                "content": SLIDES_JSON_USER_PROMPT.replace("{topic}", message),
            }
        )

        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = await llm.ainvoke(llm_messages)
                raw = resp.content if isinstance(resp.content, str) else str(resp.content)
                json_text = extract_json_object(raw)
                data = json.loads(json_text)
                normalized = normalize_slide_data(data, topic=message)
                if normalized["slides"]:
                    return normalized
                logger.warning("slides_json_empty", attempt=attempt)
            except json.JSONDecodeError as e:
                logger.warning(
                    "slides_json_parse_failed",
                    attempt=attempt,
                    error=str(e),
                    raw_head=raw[:200] if "raw" in locals() else "",
                )
            except Exception as e:
                logger.warning("slides_json_llm_failed", attempt=attempt, error=str(e))

        logger.error("slides_generation_all_retries_failed", topic=message[:80])
        return normalize_slide_data({}, topic=message)  # 触发 fallback

    async def _save_artifact(
        self, artifact_id: str, slides_data: dict, kwargs: dict
    ) -> None:
        """将 slides JSON 持久化到 MinIO。"""
        from lea_server.services.object_storage import get_object_storage

        session_id = kwargs.get("session_id") or uuid.uuid4().hex
        safe_session_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", session_id)
        payload = {
            "artifact_id": artifact_id,
            "type": "slides",
            "session_id": session_id,
            **slides_data,
        }
        key = f"artifacts/{safe_session_id}/{artifact_id}.json"
        data = json.dumps(payload, ensure_ascii=False).encode()
        get_object_storage().put_object(key, data, "application/json")
        logger.info(
            "slides_artifact_saved", key=key, pages=len(slides_data["slides"])
        )
