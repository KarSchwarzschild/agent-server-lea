"""安全流水线 —— 组合 PII / injection / moderator，供 supervisor 节点单点调用。

**分工前提**：上游中转平台已做通用违规审核 + 通用注入拦截 + 云 API 前 PII 脱敏 +
调用级审计。本 pipeline 承担中转平台**在链路上救不了**的四件事：

1. **落业务库前 PII 脱敏** —— chat_message.content 写入 DB 时用脱敏版
2. **业务级审计脱敏** —— audit_log 记录脱敏文本 + PII 命中类型 hash
3. **业务特化注入拦截** —— 窃取 system prompt / 突破 agent 边界（中转不知业务规则）
4. **UI 回显脱敏** —— 管理员在审计后台看用户消息时看脱敏版

**核心策略：审计脱敏而非传输脱敏**

- LLM 收到的是原文（否则 tool_call 拿到脱敏参数会查不到数据 —— 例如"查学号
  2021301004512345"若被误当银行卡脱敏，get_student_info 直接失败）
- 审计日志、UI 回显给管理员看的是脱敏版

两个钩子：
- run_before_llm(text) —— 用户消息进 LLM 前
    顺序：业务注入检测（原文）→ 业务黑名单（原文）→ PII 脱敏（供审计）
    命中拦截 → action=BLOCK，text_for_llm 保留原文但调用方应短路
    未拦截 → text_for_llm=原文、text_for_audit=脱敏
- run_after_llm(text) —— LLM 输出返给用户前
    顺序：PII 脱敏 → 业务黑名单（看脱敏后文本）
    LLM 生成的 PII 会脱敏后再展示，防止外泄；被审核拦截则用替代文案覆盖
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lea_graph.security.injection import (
    InjectionAction,
    InjectionPolicy,
    InjectionVerdict,
    detect_injection,
    evaluate_injection_action,
)
from lea_graph.security.moderation import BaseModerator, ModerationVerdict
from lea_graph.security.pii import PIIScrubber, ScrubResult


@dataclass
class SecurityContext:
    """安全上下文 —— 从 config.configurable 或 state 传入。"""

    tenant_id: str = "default"
    user_id: str = ""
    session_id: str = ""
    injection_policy: InjectionPolicy = field(default_factory=InjectionPolicy)


@dataclass
class BeforeLLMResult:
    action: InjectionAction = InjectionAction.ALLOW
    text_for_llm: str = ""       # 喂给 LLM 的文本 —— 原文（未脱敏），保证 tool_call 参数完整
    text_for_audit: str = ""     # 落审计日志/管理员回显 —— 脱敏版
    original_length: int = 0
    injection: InjectionVerdict | None = None
    pii: ScrubResult | None = None
    moderation: ModerationVerdict | None = None
    block_reason: str = ""

    @property
    def blocked(self) -> bool:
        return (
            self.action == InjectionAction.BLOCK
            or (self.moderation is not None and self.moderation.blocked)
        )


@dataclass
class AfterLLMResult:
    text_for_user: str = ""      # 展示给用户 —— 脱敏（防 LLM 泄露 PII）
    text_for_audit: str = ""     # 落审计日志 —— 脱敏（同 text_for_user）
    original: str = ""           # LLM 原始输出（不落库，仅内部审阅用）
    pii: ScrubResult | None = None
    moderation: ModerationVerdict | None = None
    replaced: bool = False


class SecurityPipeline:
    """组合安全器件 —— 单例注入 supervisor / API 中间件。"""

    def __init__(
        self,
        scrubber: PIIScrubber | None = None,
        input_moderators: list[BaseModerator] | None = None,
        output_moderators: list[BaseModerator] | None = None,
    ):
        self._scrubber = scrubber or PIIScrubber()
        self._input_mods = list(input_moderators or [])
        self._output_mods = list(output_moderators or [])

    async def run_before_llm(
        self,
        text: str,
        ctx: SecurityContext,
    ) -> BeforeLLMResult:
        raw = text or ""
        result = BeforeLLMResult(
            text_for_llm=raw,
            text_for_audit=raw,
            original_length=len(raw),
        )

        # 1. 注入检测（用原文，命中即拦截）
        raw_verdict = detect_injection(raw)
        verdict = evaluate_injection_action(raw_verdict, ctx.injection_policy)
        result.injection = verdict
        result.action = verdict.action

        if verdict.action == InjectionAction.BLOCK:
            result.block_reason = verdict.reason or "prompt injection detected"
            return result

        # 2. 输入审核（用原文，让审核器看到完整语义）
        for mod in self._input_mods:
            m = await mod.check_input(raw)
            if m.blocked:
                result.moderation = m
                result.action = InjectionAction.BLOCK
                result.block_reason = m.reason or "input moderation blocked"
                return result
            if not result.moderation or m.severity == "medium":
                result.moderation = m

        # 3. PII 脱敏（只影响 text_for_audit，不动 text_for_llm）
        pii_result = self._scrubber.scrub(raw)
        result.pii = pii_result
        result.text_for_audit = pii_result.scrubbed

        return result

    async def run_after_llm(
        self,
        text: str,
        ctx: SecurityContext,
    ) -> AfterLLMResult:
        raw = text or ""

        # 1. LLM 输出的 PII 一律脱敏（防止外泄）
        pii_result = self._scrubber.scrub(raw)
        result = AfterLLMResult(
            text_for_user=pii_result.scrubbed,
            text_for_audit=pii_result.scrubbed,
            original=raw,
            pii=pii_result,
            replaced=pii_result.had_hits,
        )

        # 2. 输出审核（看脱敏后的文本，避免把 PII 再传给审核器）
        for mod in self._output_mods:
            m = await mod.check_output(result.text_for_user)
            if m.blocked:
                result.moderation = m
                result.text_for_user = "很抱歉，本次生成内容因安全策略被屏蔽。"
                result.text_for_audit = result.text_for_user
                result.replaced = True
                return result
            if not result.moderation or m.severity == "medium":
                result.moderation = m

        return result
