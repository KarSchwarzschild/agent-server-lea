"""上下文窗口从 llm_providers.model_limits 下发给 lea-agent。

这个数决定引擎什么时候开始压缩历史。它错了**不会报错**，只会表现成别的症状：

    填小了   压缩跑得比必要更早，每次一个摘要模型调用，白花钱
    填大了   历史加载到超过模型能吃的量，在 provider 那里整轮失败
    没下发   lea-agent 走保守兜底，行为正确但保守

所以这个文件盯的全是「静默走偏」的路径，而不是接口能不能通。

它替换的是 Go 侧一个按模型名前缀匹配的 switch（contextWindowFor）。那套的毛病是
模型名多一个后缀就静默落进缺省分支 —— 这里刻意**不做前缀匹配**，查不到就返 0
交给兜底，因为兜底那条路径至少是明确的。
"""

from __future__ import annotations

import pytest
from lea_config.api.internal import _context_window_of
from lea_config.models.llm_provider import LLMProvider


def provider(limits) -> LLMProvider:
    return LLMProvider(
        name="p", base_url="http://x", api_key="k",
        models="qwen3-max,glm-5.1", model_limits=limits,
    )


def test_reads_the_configured_window():
    assert _context_window_of(provider({"qwen3-max": 258048}), "qwen3-max") == 258048


def test_each_model_gets_its_own_number():
    """换掉的那版把 qwen3-max 和 glm-5.1 都落进同一个 128000 缺省。

    两个真实窗口分别是 258048 和 ~200000，也就是**两个都偏小一半以上** —— 于是
    每个会话都比必要更早地摘要一次。这条钉住"按模型分"这件事真的生效了。
    """
    p = provider({"qwen3-max": 258048, "glm-5.1": 200000})
    assert _context_window_of(p, "qwen3-max") == 258048
    assert _context_window_of(p, "glm-5.1") == 200000


def test_unknown_model_returns_zero_not_a_default():
    """查不到返 0，**不在这里塞缺省**。

    0 的含义是"这里没有答案"，缺省属于 lea-agent（session.contextBudget）。
    两边各有一个缺省的话它们迟早不一样，而不一样的表现是"压缩时机跟你以为的不同"。
    """
    assert _context_window_of(provider({"qwen3-max": 258048}), "some-new-model") == 0


def test_no_limits_configured_returns_zero():
    """model_limits 为 NULL 是现网迁移前的状态，必须安全降级而不是抛。"""
    assert _context_window_of(provider(None), "qwen3-max") == 0


def test_no_prefix_matching():
    """刻意不做前缀匹配 —— 这正是被换掉的那套的毛病。

    "qwen3-max-2026-01" 不该自动继承 "qwen3-max" 的窗口：新版本的窗口可能变了
    （qwen3.7-max 就从 256K 涨到了 1M），而静默继承一个过时的数不会有任何提示。
    查不到走兜底，兜底是保守的，最坏结果只是压缩早跑一次。
    """
    p = provider({"qwen3-max": 258048})
    assert _context_window_of(p, "qwen3-max-2026-01") == 0


@pytest.mark.parametrize("bad", ["很多", None, -1, {"nested": 1}, [1, 2]])
def test_malformed_value_falls_back_to_zero(bad):
    """配置里填了非数字/负数 —— 走兜底，不要让一次手滑把整个会话打挂。"""
    assert _context_window_of(provider({"qwen3-max": bad}), "qwen3-max") == 0


def test_malformed_limits_column_falls_back_to_zero():
    """model_limits 整个不是 dict（比如有人塞了个字符串）。"""
    for bad in ("qwen3-max=258048", [258048], 258048):
        assert _context_window_of(provider(bad), "qwen3-max") == 0


def test_window_rides_along_with_base_url_and_key():
    """下发契约：窗口跟 base_url / api_key 在同一个 llm 子对象里。

    lea-agent 的 config_loader 就是按这个形状解的（raw.LLM.ModelContextWindow）。
    挪到别处去的话 Go 侧解不到，然后**静默**走兜底 —— 又是一个没有报错的降级。
    """
    import inspect

    from lea_config.api import internal

    src = inspect.getsource(internal.get_agent_config)
    assert '"model_context_window"' in src, "窗口没有下发"
    # 跟 base_url 在同一个字典字面量里
    llm_block = src[src.index('"llm_base_url"'):]
    assert '"model_context_window"' in llm_block[:400], "窗口不在 llm 子对象里"
