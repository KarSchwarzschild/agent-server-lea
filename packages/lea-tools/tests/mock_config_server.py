"""最小 mock lea-config server — 供全链路集成测试用。

返回 Go agent 期望的 AgentConfig 格式，包含 sandbox_enabled=true + skills。
"""

from fastapi import FastAPI

app = FastAPI()


@app.get("/internal/agent-config/{agent_id}")
def get_agent_config(agent_id: int, tenant_id: str = "default"):
    return {
        "agent": {
            "id": agent_id,
            "name": "数据分析助手",
            "model": "qwen3-max",
            "max_tokens": 4096,
            "max_turns": 10,
            "temperature": 0.7,
            "kb_mode": "off",
            "memory_mode": "off",
            "system_prompt": (
                "你是一个多技能助手。当用户需要计算或数据处理时，"
                "使用 code_interpreter 工具。当用户需要制作PPT时，"
                "先调用 activate_skill 激活对应技能。"
            ),
            "sandbox_enabled": True,
            "active_skill_id": "",
            "tenant_id": tenant_id,
        },
        "kb_ids": [],
        "skills": [
            {
                "id": 1,
                "name": "pptx-generator",
                "display_name": "PPT Generator",
                "description": "生成、编辑和读取 PowerPoint 演示文稿",
                "system_prompt": (
                    "# PPTX Generator\n\n"
                    "你现在是 PPT 生成专家。按以下步骤工作：\n"
                    "1. 使用 code_interpreter 执行 /workspace/skill/scripts/ 下的脚本\n"
                    "2. 最终输出保存到 /workspace/skill/output/\n\n"
                    "可用脚本：\n"
                    "- scripts/create_pptx.js: PptxGenJS 创建新PPT\n"
                    "- scripts/validate.py: 验证生成的PPT文件\n"
                ),
                "bundle_uri": "s3://lea-uploads/skills/default/pptx-generator/abc123.zip",
                "required_tools": [],
                "priority": 1,
            },
            {
                "id": 2,
                "name": "data-analysis",
                "display_name": "数据分析",
                "description": "数据清洗、统计分析和可视化",
                "system_prompt": (
                    "# Data Analysis Skill\n\n"
                    "你现在是数据分析专家。使用 pandas/matplotlib 完成分析任务。\n"
                ),
                "bundle_uri": "",
                "required_tools": [],
                "priority": 2,
            },
        ],
        "tools": [],
        "team": None,
        "llm": {
            "llm_base_url": "https://tenantapi-ai.ouchn.edu.cn/v1",
            "llm_api_key": "tenant-158d1e1389ed4de5831325324c3a514e",
        },
    }


@app.get("/health")
def health():
    return {"status": "ok", "service": "mock-lea-config"}
