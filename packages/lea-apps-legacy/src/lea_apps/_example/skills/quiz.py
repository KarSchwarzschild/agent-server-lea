"""示例App专属Skill: 测验生成。"""

from langchain_core.tools import tool


@tool
def generate_quiz(topic: str, count: int = 5) -> str:
    """根据指定主题生成测验题目。适用于教育场景的随堂测验。"""
    questions = []
    for i in range(1, count + 1):
        questions.append(
            f"{i}. 关于「{topic}」的问题{i}：请简要论述你的理解。\n"
            f"   A) 选项A  B) 选项B  C) 选项C  D) 选项D"
        )
    return f"## {topic} — 测验（{count}题）\n\n" + "\n\n".join(questions)
