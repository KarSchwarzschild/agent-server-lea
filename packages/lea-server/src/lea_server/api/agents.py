"""Agents 端点 stub — SuperAgent 前端的 agent 选择器最小依赖。

lea-platform 当前没有独立 Agent 概念（只有 App/Skill/Supervisor），本 stub
提供一个默认 "Super Agent" 让前端 selector/plaza 页面能正常渲染。

未来 Ph2/Ph3 引入 Agent 概念时再替换为真实实现。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from lea_server.core.deps import get_current_user
from lea_server.models.models import User

router = APIRouter(prefix="/agents", tags=["agents"])


_DEFAULT_AGENT = {
    "id": 0,
    "name": "Super Agent",
    "description": "通用超级智能助手",
    "avatar": "🤖",
    "welcome_message": "你好！我可以帮你搜索资料、生成课件、答疑、辩论等等。",
    "soul_prompt": None,
    "category": "general",
    "category_label": "通用",
    "is_system": True,
    "is_mine": False,
    "is_enabled": True,
    "sort_order": 0,
    "allowed_skills": None,
    "example_questions": [
        "帮我梳理一下最近的教育技术趋势",
        "生成一份《人工智能导论》第一章课件",
        "对比两种混合学习模型的优劣",
    ],
}


@router.get("")
def list_agents(
    scope: str = "all",
    category: str | None = None,
    search: str | None = None,
    _: User = Depends(get_current_user),
):
    """返回 agent 列表 —— stub 版只有一个默认 super agent。"""
    return {"items": [_DEFAULT_AGENT]}


@router.get("/categories")
def get_categories(_: User = Depends(get_current_user)):
    """AgentPlaza 用的分类映射（stub）。"""
    return {"general": "通用"}


@router.get("/super-agent-categories")
def get_super_agent_categories(_: User = Depends(get_current_user)):
    """SuperAgent selector 用的分类映射（stub）。"""
    return {"general": "通用"}


@router.get("/default")
def get_default_agent(_: User = Depends(get_current_user)):
    """获取当前用户的默认 agent_id（stub 版永远是 0）。"""
    return {"agent_id": 0}


@router.put("/default")
def set_default_agent(_: User = Depends(get_current_user)):
    """设置默认 agent（stub 版不真存储，直接返回）。"""
    return {"agent_id": 0}


@router.delete("/default")
def clear_default_agent(_: User = Depends(get_current_user)):
    """清除默认 agent。"""
    return {"agent_id": None}


@router.get("/{agent_id}")
def get_agent(agent_id: int, _: User = Depends(get_current_user)):
    """按 id 获取单个 agent（stub 只认 0）。"""
    if agent_id == 0:
        return _DEFAULT_AGENT
    return {"error": "agent not found"}, 404
