"""SlideGeneratorPipeline —— 验证 JSON 输出 + artifact_* 事件流。

不打真实 LLM，用 mock llm_factory 返回预制 JSON；
不打真实 MinIO，monkeypatch object_storage 为 no-op。
"""

from __future__ import annotations

import json

import pytest


class _FakeLLMResponse:
    def __init__(self, content: str):
        self.content = content


class _FakeLLM:
    def __init__(self, content: str):
        self._content = content

    async def ainvoke(self, messages):
        return _FakeLLMResponse(self._content)


@pytest.fixture(autouse=True)
def _mock_object_storage(monkeypatch):
    """跳过 MinIO 持久化。"""
    from lea_server.services import object_storage as os_mod

    class _NoopStorage:
        def put_object(self, key, data, content_type):
            pass

    monkeypatch.setattr(os_mod, "get_object_storage", lambda: _NoopStorage())


async def _collect_events(pipeline, message, llm_content, **kwargs):
    """跑 pipeline 收集所有 SkillEvent。"""
    fake_llm = _FakeLLM(llm_content)
    llm_factory = lambda purpose="chat": fake_llm

    events = []
    async for ev in pipeline.run(
        definition=None,
        message=message,
        llm_factory=llm_factory,
        **kwargs,
    ):
        events.append(ev)
    return events


async def test_pipeline_emits_artifact_create_delta_done_in_order():
    """正常路径：LLM 产合法 JSON → 依次 artifact_create / delta / done / result。"""
    from lea_graph.skills.pipelines.slide_generator import SlideGeneratorPipeline

    llm_output = json.dumps({
        "version": 1,
        "theme": {"id": "edu-warm"},
        "metadata": {"title": "深度学习入门"},
        "slides": [
            {"layout": "title", "title": "深度学习入门", "elements": [],
             "speaker_notes": "开场"},
            {"layout": "content", "title": "什么是神经网络",
             "elements": [{"type": "text", "content": "由神经元组成的图"}],
             "speaker_notes": "讲清楚"},
        ],
    }, ensure_ascii=False)

    pipeline = SlideGeneratorPipeline()
    events = await _collect_events(pipeline, "深度学习", llm_output)

    types = [e.type for e in events]
    # 期望：progress → artifact_create → artifact_delta → artifact_done → result
    assert "artifact_create" in types
    assert "artifact_delta" in types
    assert "artifact_done" in types
    assert "result" in types

    # create 在 delta 之前，delta 在 done 之前
    assert types.index("artifact_create") < types.index("artifact_delta")
    assert types.index("artifact_delta") < types.index("artifact_done")


async def test_pipeline_delta_payload_is_valid_json_matching_frontend_schema():
    """artifact_delta.delta 必须是可解析的 JSON，且含前端要求的字段。"""
    from lea_graph.skills.pipelines.slide_generator import SlideGeneratorPipeline

    llm_output = json.dumps({
        "slides": [
            {"layout": "title", "title": "A", "elements": [], "speaker_notes": "x"},
        ],
    })

    pipeline = SlideGeneratorPipeline()
    events = await _collect_events(pipeline, "A", llm_output)

    delta_ev = next(e for e in events if e.type == "artifact_delta")
    parsed = json.loads(delta_ev.data["delta"])

    assert parsed["version"] == 1
    assert isinstance(parsed["slides"], list)
    assert parsed["slides"][0]["layout"] == "title"
    assert "theme" in parsed  # normalize 补默认
    assert "metadata" in parsed


async def test_pipeline_artifact_id_stable_across_events():
    """create / delta / done / result 中的 artifact_id 必须一致。"""
    from lea_graph.skills.pipelines.slide_generator import SlideGeneratorPipeline

    llm_output = json.dumps({
        "slides": [{"layout": "title", "title": "A", "elements": [], "speaker_notes": ""}],
    })

    pipeline = SlideGeneratorPipeline()
    events = await _collect_events(pipeline, "A", llm_output)

    ids = {
        e.data.get("artifact_id")
        for e in events
        if e.data.get("artifact_id")
    }
    assert len(ids) == 1, f"artifact_id 应全程一致，实际 {ids}"


async def test_pipeline_coerces_invalid_layout_to_content():
    """LLM 返回白名单外 layout → normalize 强制成 content。"""
    from lea_graph.skills.pipelines.slide_generator import SlideGeneratorPipeline

    llm_output = json.dumps({
        "slides": [
            {"layout": "bento_grid", "title": "X", "elements": [], "speaker_notes": ""},
            {"layout": "timeline", "title": "Y", "elements": [], "speaker_notes": ""},
        ],
    })

    pipeline = SlideGeneratorPipeline()
    events = await _collect_events(pipeline, "topic", llm_output)

    done_ev = next(e for e in events if e.type == "artifact_done")
    parsed = json.loads(done_ev.data["content"])
    assert all(s["layout"] == "content" for s in parsed["slides"])


async def test_pipeline_falls_back_when_llm_returns_garbage():
    """LLM 返回完全非 JSON → 走 fallback 3 页占位，不崩。"""
    from lea_graph.skills.pipelines.slide_generator import SlideGeneratorPipeline

    pipeline = SlideGeneratorPipeline()
    events = await _collect_events(pipeline, "topic", "this is not json at all")

    done_ev = next(e for e in events if e.type == "artifact_done")
    parsed = json.loads(done_ev.data["content"])
    assert len(parsed["slides"]) >= 1
    # fallback 里必须至少有一页 title 布局
    assert parsed["slides"][0]["layout"] == "title"


async def test_pipeline_strips_markdown_json_fence():
    """LLM 包了 ```json ... ``` → extract_json_object 应剥掉。"""
    from lea_graph.skills.pipelines.slide_generator import SlideGeneratorPipeline

    payload = json.dumps({
        "slides": [{"layout": "content", "title": "OK", "elements": [], "speaker_notes": ""}],
    })
    llm_output = f"```json\n{payload}\n```"

    pipeline = SlideGeneratorPipeline()
    events = await _collect_events(pipeline, "t", llm_output)

    done_ev = next(e for e in events if e.type == "artifact_done")
    parsed = json.loads(done_ev.data["content"])
    assert parsed["slides"][0]["title"] == "OK"


async def test_pipeline_artifact_type_is_slides():
    """artifact_create.artifact_type 必须是 'slides'（前端 Phase B dispatch 依赖）。"""
    from lea_graph.skills.pipelines.slide_generator import SlideGeneratorPipeline

    llm_output = json.dumps({
        "slides": [{"layout": "title", "title": "A", "elements": [], "speaker_notes": ""}],
    })

    pipeline = SlideGeneratorPipeline()
    events = await _collect_events(pipeline, "A", llm_output)

    create_ev = next(e for e in events if e.type == "artifact_create")
    assert create_ev.data["artifact_type"] == "slides"
