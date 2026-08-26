"""启动 Seed — 注册内置云端工具。

在 Tool Gateway 首次启动时调用，确保基础工具定义存在。
幂等：已存在则跳过。
"""

from lea_tools.db import SessionLocal
from lea_tools.db.models import GLOBAL_TENANT_ID, Tool


BUILTIN_TOOLS = [
    {
        "name": "kb_search",
        "display_name": "知识库检索",
        "description": "在绑定的知识库中搜索相关内容，返回匹配的文档片段。",
        "category": "knowledge",
        "execution_type": "internal",
        "schema_json": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索查询"},
                "kb_ids": {"type": "array", "items": {"type": "integer"}, "description": "知识库 ID 列表"},
                "top_k": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
        "endpoint": "/kb/search",
    },
    {
        "name": "memory_search",
        "display_name": "记忆检索",
        "description": "检索用户的长期记忆（偏好、事实、历史）。",
        "category": "knowledge",
        "execution_type": "internal",
        "schema_json": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索查询"},
                "user_id": {"type": "string"},
                "top_k": {"type": "integer", "default": 5},
            },
            "required": ["query", "user_id"],
        },
        "endpoint": "/memory/search",
    },
    {
        "name": "memory_add",
        "display_name": "记忆写入",
        "description": "将新的用户偏好或事实写入长期记忆。",
        "category": "knowledge",
        "execution_type": "internal",
        "schema_json": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "content": {"type": "string", "description": "要记住的内容"},
            },
            "required": ["user_id", "content"],
        },
        "endpoint": "/memory/add",
    },
    {
        "name": "web_search",
        "display_name": "联网搜索",
        "description": "搜索互联网获取最新信息。",
        "category": "search",
        "execution_type": "api",
        "schema_json": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "top_k": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
        "http_adapter_config": {
            "endpoint": "https://serpapi.com/search.json",
            "method": "GET",
            "headers_template": {},
            "request_mapping": {
                "body": {
                    "q": "{{query}}",
                    "num": "{{top_k}}",
                    "api_key": "{{api_key}}",
                }
            },
            "response_mapping": {"result": "$.organic_results"},
        },
    },
    {
        "name": "code_interpreter",
        "display_name": "代码解释器",
        "description": "在沙箱环境中执行代码，支持 Python/JS/Shell。",
        "category": "code",
        "execution_type": "sandbox",
        "schema_json": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "要执行的代码"},
                "language": {"type": "string", "enum": ["python", "javascript", "bash"], "default": "python"},
            },
            "required": ["code"],
        },
        "sandbox_config": {
            "template": "default",
            "timeout": 30000,
            "memory_mb": 512,
        },
    },
]


def seed_builtin_tools():
    """幂等注册内置工具 — 已存在则跳过。"""
    db = SessionLocal()
    try:
        created = 0
        for tool_def in BUILTIN_TOOLS:
            # 内置工具显式落在 GLOBAL_TENANT_ID —— 它们对所有租户可见（见
            # db/models.py 里 GLOBAL_TENANT_ID 的说明）。以前不带 tenant 建也不带
            # tenant 查，靠的是全局 UNIQUE(name) 恰好保证唯一，那是巧合不是设计。
            existing = (
                db.query(Tool)
                .filter(
                    Tool.tenant_id == GLOBAL_TENANT_ID,
                    Tool.name == tool_def["name"],
                )
                .first()
            )
            if existing:
                continue
            # is_builtin=True 必须在这里写。这一列是接口层"内置不可改不可删"那道门的
            # 唯一依据（lea-config 的 _reject_if_builtin / _reject_name_conflict），
            # 而这个函数是内置行的**唯一**生产者 —— 不写的话新库里种出来的 5 行
            # is_builtin=0，两道门在生产环境有效、在任何新环境静默失效。
            # 现网那 5 行已经是 1（老 lea-server 种的），所以只影响新库，也正因如此
            # 更容易漏。
            tool = Tool(tenant_id=GLOBAL_TENANT_ID, is_builtin=True, **tool_def)
            db.add(tool)
            created += 1
        if created:
            db.commit()
        return created
    finally:
        db.close()


if __name__ == "__main__":
    n = seed_builtin_tools()
    print(f"Seeded {n} builtin tools (skipped {len(BUILTIN_TOOLS) - n} existing)")
