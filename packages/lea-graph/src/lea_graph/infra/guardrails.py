"""输入/输出安全过滤（骨架实现）。"""

from __future__ import annotations

import re

import structlog

logger = structlog.get_logger()

_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"you\s+are\s+now\s+a",
    r"system\s*:\s*",
    r"<\|im_start\|>",
]

_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)


async def check_input(message: str) -> tuple[bool, str | None]:
    """检查用户输入。

    Returns:
        (通过, 拒绝原因) — 通过时原因为None
    """
    if _INJECTION_RE.search(message):
        logger.warning("input_blocked", reason="possible_injection")
        return False, "输入包含不允许的内容"

    if len(message) > 10_000:
        return False, "输入过长（最多10000字符）"

    return True, None


async def check_output(response: str) -> str:
    """过滤Agent输出。当前为透传，后续可添加敏感内容过滤。"""
    return response
