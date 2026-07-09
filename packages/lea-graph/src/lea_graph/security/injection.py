"""Prompt 注入检测 —— 应用层的纵深防御 + 业务特化。

**分工前提**：通用 Prompt 注入拦截（如 `ignore previous instructions` 泛化匹配）
交给上游中转平台做。本模块只承担两件事：

1. **纵深防御**：中转漏检时兜底（大模型越狱是持续研究话题，多一道拦截多一份安全）
2. **业务特化**：拦"窃取本平台 system prompt"、"绕过 agent 授权边界"、"覆盖业务开关"
   这类**中转不知道你业务规则**的攻击

策略：关键词命中 + 简单启发式，产出 0.0-1.0 风险分。分级动作由
evaluate_injection_action 决定：ALLOW / WARN / BLOCK。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class InjectionAction(str, Enum):
    ALLOW = "allow"
    WARN = "warn"
    BLOCK = "block"


@dataclass
class InjectionPolicy:
    """租户/App 级注入检测策略。"""

    mode: str = "warn"  # "off" / "warn" / "strict"
    threshold_warn: float = 0.3
    threshold_block: float = 0.7


@dataclass
class InjectionVerdict:
    risk_score: float
    matched_patterns: list[str] = field(default_factory=list)
    action: InjectionAction = InjectionAction.ALLOW
    reason: str = ""


# ── 通用注入（纵深防御，中转应该已拦）────────────────
_GENERIC_HIGH_RISK = [
    (re.compile(r"(?i)ignore (?:all |the |your )?previous instructions?"), 0.7, "ignore-prev-en"),
    # CN 版：允许"之前|上面|所有|的"等修饰词任意组合出现（修 bug：原正则不支持"之前的所有指令"）
    (re.compile(r"忽略(?:之前|上面|前面|所有|全部|你的|这些|之上|的|\s){0,10}(?:指令|命令|规则|提示|设定|要求|限制)"), 0.7, "ignore-prev-cn"),
    (re.compile(r"(?i)disregard (?:all |the |your )?(?:above|previous|prior|prompt)"), 0.7, "disregard-en"),
    (re.compile(r"(?i)you are (?:now|actually) (?:a|an)"), 0.5, "role-override-en"),
    (re.compile(r"你(?:现在|其实|真正)?(?:是|扮演|变成)一个"), 0.4, "role-override-cn"),
    (re.compile(r"(?i)system\s*:\s*"), 0.4, "system-prefix"),
    (re.compile(r"(?i)</?(?:system|instructions?|prompt)\b"), 0.5, "tag-inject"),
]

_GENERIC_MEDIUM_RISK = [
    (re.compile(r"(?i)pretend (?:to be|you are)"), 0.3, "pretend"),
    (re.compile(r"(?i)from now on"), 0.2, "from-now-on"),
    (re.compile(r"(?i)developer mode|jailbreak|DAN mode"), 0.5, "known-jailbreak"),
    (re.compile(r"(?i)act as (?:if|though)"), 0.2, "act-as-if"),
]

# ── 业务特化：中转不知道你业务规则，这层必须应用层做 ──
_BUSINESS_HIGH_RISK = [
    # 窃取 system prompt / 内部指令 —— 保护 skill 定义、agent 人格、内部工具描述
    (re.compile(r"(?i)reveal (?:your |the )?(?:system prompt|instructions|rules|prompt)"), 0.7, "leak-prompt-en"),
    (re.compile(r"(?i)(?:show|print|output|expose) (?:me )?(?:your |the )?(?:system prompt|instructions|initial prompt)"), 0.7, "leak-prompt-en-alt"),
    # CN 版：修 bug——"你的"和"system"之间允许空格
    (re.compile(r"(?:打印|输出|告诉我|展示|泄露|复述|重复)\s*(?:你的|完整的|原始的|所有的)?\s*(?:system\s*prompt|系统提示词|系统指令|初始指令|内部规则)"), 0.7, "leak-prompt-cn"),
    # 突破 agent 授权边界 —— agent 只绑了 X tool，用户试图让它调 Y
    (re.compile(r"(?i)(?:disable|remove|bypass|override) (?:all |your )?(?:restrictions|limitations|safety|constraints|guardrails)"), 0.6, "bypass-bounds-en"),
    (re.compile(r"(?:禁用|移除|绕过|突破|解除|关闭)\s*(?:所有|全部|你的|这些|的)*\s*(?:限制|约束|规则|边界|安全)"), 0.6, "bypass-bounds-cn"),
]


_ALL_PATTERNS = (
    _GENERIC_HIGH_RISK + _GENERIC_MEDIUM_RISK + _BUSINESS_HIGH_RISK
)


def detect_injection(text: str) -> InjectionVerdict:
    """检测 Prompt 注入 —— 返回风险分和命中模式，不做拦截决策。"""
    if not text:
        return InjectionVerdict(risk_score=0.0)

    score = 0.0
    matched: list[str] = []

    for pattern, weight, name in _ALL_PATTERNS:
        if pattern.search(text):
            score = max(score, weight)
            matched.append(name)

    if len(matched) >= 3:
        score = min(1.0, score + 0.15)
    elif len(matched) >= 2:
        score = min(1.0, score + 0.05)

    return InjectionVerdict(risk_score=round(score, 3), matched_patterns=matched)


def evaluate_injection_action(
    verdict: InjectionVerdict,
    policy: InjectionPolicy,
) -> InjectionVerdict:
    """给定检测结果和策略，决定 ALLOW / WARN / BLOCK。

    策略：
    - mode=off → 永远 ALLOW
    - mode=warn → 命中 threshold_warn 起 WARN，命中 block 阈值也不升级为 BLOCK
    - mode=strict → 命中 warn 阈值 WARN，命中 block 阈值 BLOCK
    """
    score = verdict.risk_score

    if policy.mode == "off" or score < policy.threshold_warn:
        verdict.action = InjectionAction.ALLOW
        verdict.reason = ""
        return verdict

    if policy.mode == "warn":
        verdict.action = InjectionAction.WARN
        verdict.reason = f"命中可疑模式（risk={score:.2f}, patterns={verdict.matched_patterns}）"
        return verdict

    if score >= policy.threshold_block:
        verdict.action = InjectionAction.BLOCK
        verdict.reason = f"高风险 Prompt 注入（risk={score:.2f}, patterns={verdict.matched_patterns}）"
    else:
        verdict.action = InjectionAction.WARN
        verdict.reason = f"命中可疑模式（risk={score:.2f}, patterns={verdict.matched_patterns}）"

    return verdict
