"""公共 Tool: 代码沙箱执行。

按 session 复用同一个沙箱后端（真 E2B 场景避免 60ms 冷启），把 code / language / timeout
交给底层 Backend；SSE 事件走 langgraph.config.get_stream_writer() 通知前端 Card 起-止。

安全预算集中在 `_default_execution_config()` —— 产品决策，见函数注释。
"""

from __future__ import annotations

import asyncio
import uuid

import structlog
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.config import get_stream_writer

from lea_graph.sandbox import get_manager
from lea_graph.skills.runtime_context import get_skill_context

logger = structlog.get_logger()


def _mount_skill_scripts_if_needed(backend) -> None:
    """把当前 skill bundle 的 scripts/*.py 挂进 sandbox。

    幂等：每个 backend 记住"上次挂过的 skill hash"，同 hash skip；换 skill 才重挂。
    只挂 scripts/ 目录（references/ 是给 LLM 读的文档，不是可执行代码）。
    """
    ctx = get_skill_context()
    if ctx is None or not ctx.bundle_dir or not ctx.scripts_paths:
        return
    already = getattr(backend, "_lea_mounted_skill_hash", None)
    if already == ctx.bundle_hash:
        return

    from pathlib import Path

    root = Path(ctx.bundle_dir)
    mounted = 0
    for rel in ctx.scripts_paths:
        try:
            data = (root / rel).read_bytes()
        except FileNotFoundError:
            logger.warning("skill_script_missing", path=rel, bundle=str(root))
            continue
        try:
            backend.write_file(rel, data)
            mounted += 1
        except Exception as e:
            logger.warning("skill_script_mount_failed", path=rel, error=str(e))
    backend._lea_mounted_skill_hash = ctx.bundle_hash
    logger.info(
        "skill_scripts_mounted",
        skill=ctx.skill_name,
        count=mounted,
        total=len(ctx.scripts_paths),
        hash=ctx.bundle_hash,
    )


def _default_execution_config() -> dict:
    """沙箱默认执行预算 —— 产品决策，非纯技术。

    这四个值决定 Agent 能"跑多重"和"多安全"，是你在国开场景下的取舍：

    timeout_seconds
        单次执行超时。太短跑不完综述/大数据分析；太长会烧钱且卡住 SSE UI。
        参考：pandas 处理 5MB CSV 大约需要 5-20s；matplotlib 画图 <3s；
        无限循环兜底要能挡住 → 60s 是常见平衡点。

    max_output_bytes
        stdout+stderr 合计最大字节数。防止程序把 UI 打炸（`print` 循环，
        大 DataFrame print）。100KB 够装一份文字报告或几百行表格；
        1MB 起就要担心 SSE 传输拖慢；超过截断并给 LLM 提示"输出被截断"。

    allow_network
        沙箱是否允许触网。开=True 用户能 `pip install` 装库、能 requests
        调外部 API（能力翻倍）；关=False 只能纯计算/文件处理（更安全，
        中转平台侧可能已经限制过一层）。教育场景多数不需要触网，可关。

    allowed_languages
        `("python",)` 最保守；`("python", "shell")` 让 LLM 能跑 bash（
        文件操作、grep 等）；`("python", "shell", "node")` 全放开。
        每加一种就多一份攻击面，建议初期只 python。

    真实执行时如果 LLM 传入的参数超过这里的默认值（例如 timeout=999），
    工具会 clamp 到默认上限并 log 一条。
    """
    return {
        "timeout_seconds": 60,
        "max_output_bytes": 100_000,
        "allow_network": False,
        "allowed_languages": ("python",),
    }


def _session_id_from_config(config: RunnableConfig | None) -> str:
    """从 LangGraph config 里提 session_id；无则给个 tmp 值让沙箱勉强能跑。"""
    if not config:
        return f"tmp-{uuid.uuid4().hex[:8]}"
    configurable = config.get("configurable") or {}
    # supervisor 里的约定：thread_id 形如 "tenant:user:session"
    thread_id = configurable.get("thread_id") or ""
    if thread_id:
        return thread_id.split(":")[-1] or thread_id
    return configurable.get("session_id") or f"tmp-{uuid.uuid4().hex[:8]}"


def _format_result_for_llm(execution) -> str:
    """把 SandboxExecution 拼成给 LLM 看的文本。"""
    parts: list[str] = []
    if execution.stdout:
        parts.append(f"[stdout]\n{execution.stdout}")
    if execution.stderr:
        parts.append(f"[stderr]\n{execution.stderr}")
    if execution.artifacts:
        parts.append("[artifacts]\n" + "\n".join(execution.artifacts))
    if execution.exit_code != 0:
        parts.append(f"[exit_code] {execution.exit_code}")
    if execution.timed_out:
        parts.append("[warning] 执行超时，已被中断")
    if not parts:
        return "(沙箱执行完毕，无输出)"
    return "\n\n".join(parts)


@tool
async def sandbox_exec(
    code: str,
    language: str = "python",
    timeout: int | None = None,
    *,
    config: RunnableConfig = None,
) -> str:
    """在隔离沙箱中执行代码（Python 数据分析、文件处理、图表生成等）。

    Args:
        code: 要执行的源码。Python 就写完整可运行代码（可含 print、import matplotlib 等）；
              shell 就是一条命令。当前工作目录是该 session 独占的沙箱目录。
        language: "python" 或 "shell"。
        timeout: 秒。省略走默认预算；给的话会被 clamp 到默认上限。

    Returns:
        含 stdout / stderr / 产物文件路径 / exit_code 的多段文本。
    """
    session_id = _session_id_from_config(config)
    cfg = _default_execution_config()

    if language not in cfg["allowed_languages"]:
        return f"不允许的语言 {language!r}；本沙箱允许：{list(cfg['allowed_languages'])}"

    effective_timeout = cfg["timeout_seconds"]
    if timeout and 0 < timeout < effective_timeout:
        effective_timeout = timeout

    writer = get_stream_writer()
    call_id = f"sbx-{uuid.uuid4().hex[:8]}"

    writer({
        "type": "sandbox_start",
        "call_id": call_id,
        "language": language,
        "timeout": effective_timeout,
        "code_preview": code[:120] + ("..." if len(code) > 120 else ""),
    })

    backend = get_manager().get_or_create(session_id)
    # SG-6: 如果当前是 skill_executor 上下文里且 skill 有 scripts，挂进沙箱（幂等）
    _mount_skill_scripts_if_needed(backend)

    # 底层 subprocess/E2B 都是阻塞式，扔线程池不阻塞 event loop
    execution = await asyncio.to_thread(
        backend.run_code, code, language, effective_timeout
    )

    # 输出截断
    original_out_len = len(execution.stdout)
    original_err_len = len(execution.stderr)
    max_bytes = cfg["max_output_bytes"]
    if original_out_len > max_bytes:
        execution.stdout = execution.stdout[:max_bytes] + f"\n... (truncated {original_out_len - max_bytes} bytes)"
    if original_err_len > max_bytes:
        execution.stderr = execution.stderr[:max_bytes] + f"\n... (truncated {original_err_len - max_bytes} bytes)"

    # 前端拆分事件：每个 artifact 单独一条，方便逐个渲染
    for path in execution.artifacts:
        writer({
            "type": "sandbox_artifact",
            "call_id": call_id,
            "path": path,
        })

    writer({
        "type": "sandbox_done",
        "call_id": call_id,
        "exit_code": execution.exit_code,
        "duration_ms": execution.duration_ms,
        "timed_out": execution.timed_out,
        "stdout_preview": execution.stdout[:200],
        "stderr_preview": execution.stderr[:200],
        "artifact_count": len(execution.artifacts),
    })

    logger.info(
        "sandbox_exec_done",
        session=session_id,
        language=language,
        exit_code=execution.exit_code,
        duration_ms=execution.duration_ms,
        artifacts=len(execution.artifacts),
    )

    return _format_result_for_llm(execution)
