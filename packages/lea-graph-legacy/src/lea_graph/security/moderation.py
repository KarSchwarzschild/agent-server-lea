"""审核器协议 —— 统一接口，插件位。

**分工前提**：通用违规内容识别（涉政/暴力/色情）交给上游中转平台的云审核 API。
本模块的 KeywordModerator 定位是**租户自定义业务黑名单**，例如：
- K12 应用租户加"色情/毒品/暴力"等禁词做二次拦截
- 教师端加"考试原题/答题"等业务禁词
- 医疗应用加"处方开具"等业务禁词

不是替代云审核 —— 是补充中转平台不知道的**业务侧禁词**。

Agent 层通过 SecurityPipeline 组合多个 moderator，不关心具体实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ModerationVerdict:
    blocked: bool = False
    categories: list[str] = field(default_factory=list)
    severity: str = "low"  # low / medium / high
    reason: str = ""
    source: str = ""


class BaseModerator(Protocol):
    """审核器协议 —— 输入/输出侧统一接口。"""

    name: str

    async def check_input(self, text: str) -> ModerationVerdict: ...

    async def check_output(self, text: str) -> ModerationVerdict: ...


# ── 内置：关键词审核器（tenant 可注入自定义词库）────────

class KeywordModerator:
    """租户自定义业务黑名单审核器。

    生产用途：让每个租户在自己的 tenant_policy 里配置一批业务禁词，
    在中转平台的通用云审核之外再加一道业务侧拦截。**不是**通用违规审核的替代品。
    """

    name = "keyword"

    def __init__(
        self,
        block_keywords: list[str] | None = None,
        warn_keywords: list[str] | None = None,
    ):
        self._block = [k.lower() for k in (block_keywords or [])]
        self._warn = [k.lower() for k in (warn_keywords or [])]

    async def check_input(self, text: str) -> ModerationVerdict:
        return self._scan(text, source="keyword.input")

    async def check_output(self, text: str) -> ModerationVerdict:
        return self._scan(text, source="keyword.output")

    def _scan(self, text: str, source: str) -> ModerationVerdict:
        if not text:
            return ModerationVerdict(source=source)

        lower = text.lower()
        blocked_hits = [k for k in self._block if k in lower]
        if blocked_hits:
            return ModerationVerdict(
                blocked=True,
                categories=blocked_hits,
                severity="high",
                reason=f"命中禁词：{blocked_hits}",
                source=source,
            )
        warn_hits = [k for k in self._warn if k in lower]
        if warn_hits:
            return ModerationVerdict(
                blocked=False,
                categories=warn_hits,
                severity="medium",
                reason=f"命中警告词：{warn_hits}",
                source=source,
            )
        return ModerationVerdict(source=source)
