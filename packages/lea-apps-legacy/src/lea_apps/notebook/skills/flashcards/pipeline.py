"""闪卡生成 Pipeline — 第一个 Pipeline 模式 Skill 的验证实现。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from langchain_core.messages import HumanMessage, SystemMessage

from lea_graph.skills.pipeline import SkillEvent, SkillPipeline
from lea_graph.skills.schema import SkillDefinition


class FlashcardPipeline(SkillPipeline):
    """闪卡生成：知识检索 → LLM 生成 → 结构化输出。"""

    async def run(
        self,
        definition: SkillDefinition,
        message: str,
        *,
        tools=None,
        llm_factory=None,
        **kwargs,
    ) -> AsyncIterator[SkillEvent]:
        tools = tools or {}

        # Step 1: 知识检索
        context_text = ""
        kb_tool = tools.get("kb_search")
        if kb_tool:
            yield SkillEvent("progress", {"message": "正在检索相关知识..."})
            try:
                result = await kb_tool.ainvoke({"query": message})
                context_text = str(result) if result else ""
            except Exception as e:
                yield SkillEvent("progress", {"message": f"知识检索跳过: {e}"})

        # Step 2: 构造 prompt
        yield SkillEvent("progress", {"message": "正在生成闪卡..."})

        system_prompt = definition.system_prompt or "你是一个教育闪卡生成专家。"
        user_prompt = f"主题：{message}"
        if context_text:
            user_prompt += f"\n\n参考知识：\n{context_text[:3000]}"
        user_prompt += "\n\n请生成学习闪卡，使用 JSON 数组格式：[{\"front\": \"问题\", \"back\": \"答案\"}, ...]"

        # Step 3: 流式 LLM 生成
        if llm_factory is None:
            yield SkillEvent("error", {"message": "LLM factory not available"})
            return

        llm = llm_factory(purpose="chat")
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]

        full_text = ""
        async for chunk in llm.astream(messages):
            if chunk.content:
                full_text += chunk.content
                yield SkillEvent("content", {"text": chunk.content})

        # Step 4: 尝试解析结构化结果
        try:
            start = full_text.find("[")
            end = full_text.rfind("]") + 1
            if start >= 0 and end > start:
                cards = json.loads(full_text[start:end])
                yield SkillEvent("result", {"cards": cards, "count": len(cards)})
        except json.JSONDecodeError:
            pass

        yield SkillEvent("done", {"status": "completed"})
