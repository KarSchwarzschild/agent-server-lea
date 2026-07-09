"""Skill 运行时上下文 —— ContextVar 携带当前执行中的 skill bundle 信息。

用于跨越 LangGraph tool 调用链把 skill 的 bundle_dir / scripts_paths / bundle_hash
从 skill_executor 传给下游 sandbox_exec 之类的 tool。

生命周期：skill_executor / runner 进入时 ``enter_skill_context(defn)``，
执行完 ``exit_skill_context(token)``。tool 侧用 ``get_skill_context()`` 拿。

ContextVar 在 asyncio 里跨 await 传递（同一 Task 内），足够我们的场景 —
LangGraph tool 调用发生在 skill_executor 的 create_react_agent.ainvoke() 内部，
所以 tool 能看见外层设置的 ContextVar。
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SkillRuntimeContext:
    """当前正在执行的 skill 的 bundle 上下文。"""

    skill_name: str = ""
    bundle_dir: str = ""              # cache 里的顶层目录（含 skill.md + references/ + scripts/）
    bundle_hash: str = ""             # 用于沙箱 mount 幂等判断
    scripts_paths: list[str] = field(default_factory=list)  # 相对 bundle_dir 的 posix 路径


_current: ContextVar[SkillRuntimeContext | None] = ContextVar(
    "lea_skill_runtime_context", default=None
)


def enter_skill_context(
    *,
    skill_name: str,
    bundle_dir: str,
    bundle_hash: str,
    scripts_paths: list[str],
) -> Token[Any]:
    """把当前 skill bundle 上下文压栈。返回 token 供 exit 用。"""
    ctx = SkillRuntimeContext(
        skill_name=skill_name,
        bundle_dir=bundle_dir,
        bundle_hash=bundle_hash,
        scripts_paths=list(scripts_paths),
    )
    return _current.set(ctx)


def exit_skill_context(token: Token[Any]) -> None:
    _current.reset(token)


def get_skill_context() -> SkillRuntimeContext | None:
    return _current.get()
