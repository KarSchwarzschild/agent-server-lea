"""Agent 安全护栏 v1 —— PII 脱敏 + 注入防护 + 审核器插件位。

分层：
- pii: 敏感信息检测与脱敏（regex-first，可后续接 NER）
- injection: Prompt 注入检测与分级（关键词 + 启发式，可后续接分类器）
- moderation: 审核器协议（KeywordModerator 内置，云 API / 自建模型走同一接口）
- pipeline: 组合调用 —— before_llm（入参过滤）/ after_llm（出参过滤）
"""

from lea_graph.security.injection import (
    InjectionAction,
    InjectionPolicy,
    InjectionVerdict,
    detect_injection,
    evaluate_injection_action,
)
from lea_graph.security.moderation import (
    BaseModerator,
    KeywordModerator,
    ModerationVerdict,
)
from lea_graph.security.pii import PIIHit, PIIScrubber, ScrubResult
from lea_graph.security.pipeline import (
    AfterLLMResult,
    BeforeLLMResult,
    SecurityContext,
    SecurityPipeline,
)

__all__ = [
    "AfterLLMResult",
    "BaseModerator",
    "BeforeLLMResult",
    "InjectionAction",
    "InjectionPolicy",
    "InjectionVerdict",
    "KeywordModerator",
    "ModerationVerdict",
    "PIIHit",
    "PIIScrubber",
    "ScrubResult",
    "SecurityContext",
    "SecurityPipeline",
    "detect_injection",
    "evaluate_injection_action",
]
