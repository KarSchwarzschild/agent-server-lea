"""Demo 用平台工具：安全数学表达式求值 + 当前时间查询。

两个工具都是纯 Python 无外网依赖，用来给 ReAct skill 演示 tool_call/tool_result
可视化。calculator 用 AST 白名单避免 eval 安全隐患。
"""

from __future__ import annotations

import ast
import operator as op
from datetime import datetime

from langchain_core.tools import tool

# 允许的算子（不包含位运算、比较、逻辑等）
_ALLOWED_OPS = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.FloorDiv: op.floordiv,
    ast.Mod: op.mod,
    ast.Pow: op.pow,
    ast.USub: op.neg,
    ast.UAdd: op.pos,
}


def _safe_eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _safe_eval_node(node.body)
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)):
            raise ValueError(f"only numeric constants allowed, got {type(node.value).__name__}")
        return node.value
    if isinstance(node, ast.BinOp):
        return _ALLOWED_OPS[type(node.op)](
            _safe_eval_node(node.left), _safe_eval_node(node.right)
        )
    if isinstance(node, ast.UnaryOp):
        return _ALLOWED_OPS[type(node.op)](_safe_eval_node(node.operand))
    raise ValueError(f"unsupported syntax: {type(node).__name__}")


@tool
def calculator(expression: str) -> str:
    """精确计算数学表达式（支持 + - * / // % ** 和括号）。

    Args:
        expression: 数学表达式字符串，例如 "234 * 567" 或 "(3+5) ** 2"

    Returns:
        计算结果的字符串描述，或错误说明。
    """
    try:
        tree = ast.parse(expression.strip(), mode="eval")
        result = _safe_eval_node(tree)
        # 整型结果不带 .0 尾巴
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        return f"{expression} = {result}"
    except KeyError as e:
        return f"计算失败：不支持的运算符 {e}"
    except Exception as e:
        return f"计算失败：{e}"


@tool
def get_current_time() -> str:
    """获取服务器当前的日期和时间（本地时区）。

    Returns:
        形如 "2026-07-07 09:36:05 (Tuesday)" 的字符串。
    """
    now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S (%A)")
