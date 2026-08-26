"""对话助手Agent — 迁移自旧项目 SuperAgentOrchestrator 的QA路径。

旧架构: orchestrator 手动判断 intent → 分发到 _handle_skill() / _handle_qa()
新架构: LangGraph create_react_agent 内建 ReAct 循环，LLM自动决定是否调用工具

教学说明:
  ReAct (Reasoning + Acting) 是一种 Agent 架构模式：
  1. LLM 先推理（Reasoning）："用户问的是学术问题，需要搜索文献"
  2. 然后行动（Acting）：调用 academic_search 工具
  3. 观察结果（Observation）：得到搜索结果
  4. 继续推理：根据结果生成回答，或者调用更多工具
  5. 循环直到 LLM 认为可以给出最终回答

  create_react_agent 把这个循环内建了，我们只需要提供 LLM 和工具列表。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.prebuilt import create_react_agent

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool
    from langgraph.graph.state import CompiledStateGraph

SYSTEM_PROMPT = """你是终身教育智能科研助手。你的职责是帮助研究者和教育工作者解答学术问题、检索文献、分析教育政策。

你可以使用以下工具来帮助用户：
- 知识库搜索：检索平台已有的教育文献和资料
- 学术搜索：检索学术论文数据库
- 网页搜索：获取网上最新信息
- 文件读写：保存和读取生成的文档
- 生成PPT：当用户要求制作演示文稿时，调用 generate_pptx 工具生成 .pptx 文件
- 生成Word：当用户要求写文档/报告时，调用 generate_docx 工具生成 .docx 文件

请根据用户的问题，合理判断是否需要使用工具：
- 如果是知识性问题，先搜索再回答
- 如果是开放性讨论，直接对话即可
- 如果用户要求生成PPT或Word文档，务必调用对应的工具生成实际文件
- 回答要专业、有深度、有条理

请用中文回答。"""


def build_chat_assistant(
    llm_factory,
    platform_tools: list[BaseTool],
    app_skills: list[BaseTool],
) -> CompiledStateGraph:
    """构建对话助手Agent。

    Args:
        llm_factory: LLM工厂函数 (purpose, **kwargs) -> ChatOpenAI
        platform_tools: 平台公共Skill列表
        app_skills: Chat App专属Skill列表

    Returns:
        编译后的 LangGraph ReAct Agent
    """
    all_tools = list(platform_tools) + list(app_skills)
    model = llm_factory("chat", streaming=True, temperature=0.7)

    return create_react_agent(
        model=model,
        tools=all_tools,
        prompt=SYSTEM_PROMPT,
    )
