"""PII 检测与脱敏。

检测器（regex-first）：手机号 / 身份证 / 邮箱 / 银行卡 / API Key 疑似值。
脱敏策略：可插拔的 redactor —— 三个内置 redactor（phone / id_card / email）
签名统一为 `(raw: str) -> str`，返回脱敏后可读版本。

设计意图：
- 用于 supervisor 图的 security_precheck 节点，跑在 LLM 调用之前
- 命中的 raw 值不进入 LLM 上下文，也不落 chat_message.content 原文
- 命中信息通过 ScrubResult.hits 返回给审计日志（存 hash，不存原文）
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class PIIHit:
    """一次 PII 命中记录 —— 存 hash 不存原文。"""

    kind: str
    span: tuple[int, int]
    length: int
    value_hash: str = ""


@dataclass
class ScrubResult:
    scrubbed: str
    hits: list[PIIHit] = field(default_factory=list)

    @property
    def had_hits(self) -> bool:
        return len(self.hits) > 0

    def kinds(self) -> set[str]:
        return {h.kind for h in self.hits}


# ── Detectors ────────────────────────────────────────────

# 中国大陆手机号：1开头 11 位
_RE_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")

# 中国大陆身份证 18 位（15 位老版不做，误检率高）
_RE_ID_CARD = re.compile(r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)")

# 邮箱
_RE_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# 银行卡 16-19 位（前后不能是数字）
_RE_BANK_CARD = re.compile(r"(?<!\d)\d{16,19}(?!\d)")

# API Key 疑似值 —— sk-/pk-/ghp_/xoxb-/AKIA 开头 + 20+ 位
_RE_API_KEY = re.compile(r"\b(?:sk-|pk-|ghp_|xoxb-|AKIA)[A-Za-z0-9_\-]{20,}\b")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _luhn_check(number: str) -> bool:
    """Luhn 校验 —— 真银行卡都能通过，学号/工号极少能过。

    加这个校验能把"16-19 位纯数字学号被误当银行卡脱敏"的概率降到极低。
    """
    if not number.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed(number)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


# ── Redactors ──────────────────────────────────────────

def _redact_phone(raw: str) -> str:
    """手机号脱敏：`138****5678`（前 3 后 4，业界标配）。"""
    if len(raw) != 11:
        return "*" * len(raw)
    return f"{raw[:3]}****{raw[-4:]}"


def _redact_id_card(raw: str) -> str:
    """身份证脱敏：`110101********7890`（前 6 户籍地 + 后 4 序号+校验位）。"""
    if len(raw) != 18:
        return "*" * len(raw)
    return f"{raw[:6]}{'*' * 8}{raw[-4:]}"


def _redact_email(raw: str) -> str:
    """邮箱脱敏：`a***@example.com`（本地首字母 + 域名保留）。"""
    if "@" not in raw:
        return "*" * len(raw)
    local, _, domain = raw.partition("@")
    if not local:
        return raw
    return f"{local[0]}***@{domain}"


def _redact_bank_card(raw: str) -> str:
    """银行卡脱敏：保留前 6（BIN 号）+ 后 4。"""
    if len(raw) <= 10:
        return "*" * len(raw)
    return f"{raw[:6]}{'*' * (len(raw) - 10)}{raw[-4:]}"


def _redact_api_key(raw: str) -> str:
    """API Key 脱敏：保留前缀，其余全遮。"""
    for prefix in ("sk-", "pk-", "ghp_", "xoxb-", "AKIA"):
        if raw.startswith(prefix):
            return f"{prefix}{'*' * (len(raw) - len(prefix))}"
    return "*" * len(raw)


# ── Scrubber ────────────────────────────────────────────

# (kind, pattern, redactor, validator?)
# validator 是可选的后置校验器 —— pattern 命中后再跑一遍，False 就跳过。
# 目前只有 bank_card 用（Luhn 校验）拦掉学号/工号纯数字误命中。
_DETECTORS: list[
    tuple[str, re.Pattern, Callable[[str], str], Callable[[str], bool] | None]
] = [
    ("id_card", _RE_ID_CARD, _redact_id_card, None),
    ("phone", _RE_PHONE, _redact_phone, None),
    ("email", _RE_EMAIL, _redact_email, None),
    ("api_key", _RE_API_KEY, _redact_api_key, None),
    ("bank_card", _RE_BANK_CARD, _redact_bank_card, _luhn_check),
]


class PIIScrubber:
    """PII 脱敏器 —— 无状态，单例可复用。

    顺序敏感：先跑 id_card / api_key 这类结构强的，再跑 phone / bank_card
    这类"11 位纯数字"容易被子串误命中的。
    """

    def __init__(self, enabled_kinds: set[str] | None = None):
        self._enabled = enabled_kinds

    def scrub(self, text: str) -> ScrubResult:
        if not text:
            return ScrubResult(scrubbed=text or "")

        hits: list[PIIHit] = []
        occupied: list[tuple[int, int]] = []
        replacements: list[tuple[int, int, str]] = []

        for kind, pattern, redactor, validator in _DETECTORS:
            if self._enabled is not None and kind not in self._enabled:
                continue
            for match in pattern.finditer(text):
                start, end = match.span()
                if any(not (end <= o_s or start >= o_e) for o_s, o_e in occupied):
                    continue
                raw = match.group()
                if validator is not None and not validator(raw):
                    continue
                replaced = redactor(raw)
                replacements.append((start, end, replaced))
                occupied.append((start, end))
                hits.append(PIIHit(
                    kind=kind,
                    span=(start, end),
                    length=end - start,
                    value_hash=_hash(raw),
                ))

        if not replacements:
            return ScrubResult(scrubbed=text)

        replacements.sort(key=lambda r: r[0])
        parts: list[str] = []
        cursor = 0
        for start, end, replaced in replacements:
            parts.append(text[cursor:start])
            parts.append(replaced)
            cursor = end
        parts.append(text[cursor:])

        return ScrubResult(scrubbed="".join(parts), hits=hits)
