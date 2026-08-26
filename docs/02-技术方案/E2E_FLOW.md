# Agent 一次会话 · 全服务调用链

一次用户对话从**选中 Agent** 到 **收到最终文本**（例：让 agent 生成 xlsx 报表），涉及哪些服务、每一步 HTTP 输入输出是什么。基于 2026-07-22 真实录制。

> 平台只有 **一条** agent 主循环路径：Go lea-agent。历史 `lea_graph`（Python LangGraph）**已废弃**，不再使用。
>
> **生产链路**：前端 → lea-server（鉴权 + 会话持久化 + SSE 转发）→ lea-agent
> - `POST :8000/api/chat/sessions` 创会话
> - `POST :8000/api/chat/send` 启动 turn（异步跑）
> - `GET  :8000/api/chat/stream/:sid` SSE 长连接消费事件
>
> **调试链路**（本文用它讲时序）：`POST :8100/debug/trace` 一次性返回完整 5 阶段 trace + 最终文本。**跟生产走同一套内部代码**（LoadAgentConfig → memory.PreTurn → engine.RunTurn → memory.OnSessionEnd），差异只在响应形式（SSE 流 vs JSON 一次性）。用它讲时序更清楚，因为每步耗时 detail 直接可见。

---

## 参与服务清单

| 服务 | 地址 | 语言 | 本文档中的角色 |
|---|---|---|---|
| lea-server | :8000 | Python | 前端 API 网关（暂略，直接从 lea-agent 讲起） |
| **lea-agent** | :8100 | Go | 主 LLM 循环 + tool 调度 |
| lea-config | :8500 | Python | agent 元数据聚合器 |
| lea-rag | :8300 | Python | KB 检索 + Memory 抽取/检索/入库 |
| lea-tools | :8400 | Python | Sandbox 会话网关 + Skill bundle 分发 |
| Cube master | 10.192.241.137:3000 | Go | 沙箱调度平台 |
| 中转 API | tenantapi-ai.ouchn.edu.cn | - | LLM（qwen3-max）+ embedding |
| MySQL | 202.205.160.9:3307 | - | 主 DB |
| Milvus | :19530 | - | 向量库（KB + Memory） |
| MinIO | :9000 | - | Skill bundle 存储 |

---

## 阶段 0 · 前端准备（选 Agent）

前端上线时先拉 agent 列表：

**请求**
```http
GET /api/agents?scope=all
Host: lea-server:8000
Authorization: Bearer <jwt>
```

**响应**（lea-server 查 MySQL `agents` 表返回）
```json
{
  "items": [
    {"id": 2, "name": "test", "category": "general",
     "avatar": "", "welcome_message": "",
     "example_questions": [...]}
  ]
}
```

用户点击某个 agent → 前端记住 `agent_id=2`，进入对话页。

---

## 阶段 1 · 用户发消息

### 生产链路

```
前端 → POST :8000/api/chat/sessions   {agent_id}
       ← {session_id}
前端 → POST :8000/api/chat/send       {session_id, message}
       ← {status:"queued"}
       │
       │ lea-server 转发：
       │ POST :8100/api/chat/send  {session_id, agent_id, user_id, message}
       │
       │ lea-agent 内部：
       │   session.Manager.Enqueue(sessionID, message)
       │   异步跑 turn，事件 push 到 in-mem event bus
前端 → GET  :8000/api/chat/stream/{sid}   （SSE 长连接）
       ← event: tool_start / kb_hit / skill_activated /
                sandbox_output / assistant_chunk / assistant_done ...
```

### 调试链路（本文用它讲后续时序）

一次性 HTTP，等 turn 跑完返完整 trace + assistantText：

```http
POST /debug/trace
Host: lea-agent:8100
Content-Type: application/json

{
  "agent_id": 2,
  "user_id": "demo-user",
  "message": "帮我根据知识库生成一份 Hadoop 生态圈的 xlsx 报表，保存到 /tmp/out.xlsx"
}
```

lea-agent 收到后开始五阶段：init → pre_turn → run_turn（多轮 LLM 循环）→ session_end → 返回。**两条链路阶段 2 起完全一致**——都是同一套 `LoadAgentConfig` + `engine.RunTurn` 代码路径。

---

## 阶段 2 · init · 加载 Agent 配置

**lea-agent → lea-config**

```http
GET /internal/agent-config/2?tenant_id=default
Host: lea-config:8500
```

**响应**（lea-config 从 MySQL 聚合出 agent+kbs+skills+tools）
```json
{
  "agent": {
    "id": 2, "name": "test", "model": "qwen3-max",
    "max_turns": 20, "max_tokens": 4096,
    "kb_mode": "auto", "memory_mode": "always",
    "soul_prompt": "你是一个数据分析助手...\n1. 先用 kb_search 从知识库检索\n2. 再用 memory_search 查用户偏好\n3. 涉及 xlsx 时调 activate_skill('xlsx')\n4. 用 code_interpreter 在沙箱里生成..."
  },
  "kb_ids": [1],
  "skills": [
    {"id": 1, "name": "pptx-generator", "display_name": "Pptx Generator",
     "description": "Generate PowerPoint...", "system_prompt": "<完整 SKILL.md 内容>",
     "bundle_uri": "s3://lea-uploads/skills/pptx-generator/1.0.0.tar.gz"},
    {"id": 2, "name": "xlsx", ...},
    ...
  ],
  "tools": [],
  "llm_base_url": "https://tenantapi-ai.ouchn.edu.cn/v1",
  "llm_api_key": "tenant-xxxx"
}
```

关键点：**skill 的完整 SKILL.md 内容此时已经预加载到 agent 内存**（`SkillDef.SystemPrompt`），后续 `activate_skill` **不再回调 lea-config**。

耗时：约 **10-15ms**（MySQL join + JSON 序列化）

---

## 阶段 3 · pre_turn · 检索用户记忆

只在 `memory_mode != "off"` 时执行。

**lea-agent → lea-rag**

```http
POST /memory/search
Host: lea-rag:8300
Content-Type: application/json

{
  "user_id": "demo-user",
  "tenant_id": "default",
  "agent_id": "2",
  "query": "帮我根据知识库生成一份 Hadoop 生态圈的 xlsx 报表..."
}
```

lea-rag 内部：
1. **中转 API 拿 embedding**：`POST tenantapi-ai.ouchn.edu.cn/v1/embeddings` `{input: query, model: "bge-large-zh-v1.5"}`
2. **Milvus 向量检索** `user_memories` collection（过滤 user_id + scope）
3. **MySQL 补 metadata**（category / importance / created_at）

**响应**（有历史记忆时）
```json
[
  {"content": "用户偏好中文列名、加粗表头", "category": "preference",
   "importance": 7, "scope": "global"},
  {"content": "文件名习惯用英文+日期", "category": "preference",
   "importance": 6, "scope": "global"}
]
```

lea-agent 把命中的 memory 拼成 `[相关记忆]\n- ...\n- ...` 加到 system prompt 前面。首轮无历史时返回 `[]`，跳过注入。

耗时：**700-900ms**（主要是 embedding 请求）

---

## 阶段 4 · run_turn · LLM 主循环

lea-agent 构建 engine 配置：

```go
engine.Config{
  Provider:      "openai",
  Model:         "qwen3-max",
  BaseURL:       "https://tenantapi-ai.ouchn.edu.cn/v1",
  APIKey:        "tenant-xxxx",
  MaxIterations: 20,
  SystemPrompt:  soul_prompt + "\n\n## 可用技能\n- xlsx: ...\n- pptx: ...",
}
```

给 LLM 5 个 tool（Anthropic tool-use schema，转成 OpenAI function-calling）：
`kb_search` / `memory_search` / `memory_add` / `activate_skill` / `code_interpreter`

然后 LLM 逐 iteration 循环，直到不再 tool_call：

```
┌──────── LLM ────────┐        ┌──── Go agent ────┐        ┌── 外部服务 ──┐
│                     │        │                   │        │             │
│ iter 1: 需要查 KB    │  tool  │ 拦截 kb_search    │  HTTP  │  lea-rag    │
│ tool_call: kb_search│──────>│ 转发调用          │──────>│  :8300      │
│   {query, kb_ids}   │  call  │                   │        │             │
│                     │        │                   │  hits  │             │
│                     │        │                   │<──────│             │
│                     │  msg   │ tool_result 塞回  │        │             │
│                     │<──────│ history           │        │             │
│                     │        │                   │        │             │
│ iter 2: 激活 xlsx    │        │ (纯内存查 skills) │        │             │
│ tool_call: activate │──────>│ 返回 SKILL.md     │        │             │
│   {skill_name:xlsx} │        │                   │        │             │
│                     │        │ 异步触发 bundle    │  HTTP  │  lea-tools  │
│                     │        │ prepare-bundle   │······>│  :8400      │
│                     │        │                   │        │  → MinIO    │
│                     │        │                   │        │             │
│ iter 3: 跑代码       │        │ ensureSandbox    │  HTTP  │  lea-tools  │
│ tool_call: code_intr│──────>│ (首次: 创建 sbx)  │──────>│  :8400      │
│   {code: "pip..."}  │        │ 然后 exec        │        │  → Cube     │
│                     │        │                   │  out   │             │
│                     │<──────│                   │<──────│             │
│                     │        │                   │        │             │
│ iter 4-9: 反复迭代... │        │                   │        │             │
│                     │        │                   │        │             │
│ iter 10: 完成         │        │                   │        │             │
│ 输出最终 text        │──────>│ 存 assistantText  │        │             │
└─────────────────────┘        └───────────────────┘        └─────────────┘
```

### 4a. 每次 LLM 调用是这样打的

lea-agent → 中转 API：

```http
POST https://tenantapi-ai.ouchn.edu.cn/v1/chat/completions
Authorization: Bearer tenant-xxxx
Content-Type: application/json

{
  "model": "qwen3-max",
  "max_tokens": 4096,
  "temperature": 0.7,
  "messages": [
    {"role":"system","content":"你是一个数据分析助手..."},
    {"role":"user","content":"帮我生成 Hadoop 报表..."},
    {"role":"assistant","content":null,"tool_calls":[
      {"id":"call_1","type":"function","function":{"name":"kb_search","arguments":"{\"query\":\"Hadoop 生态圈\"}"}}
    ]},
    {"role":"tool","tool_call_id":"call_1","content":"[{\"content\":\"HDFS...\",...}]"}
  ],
  "tools": [
    {"type":"function","function":{"name":"kb_search","description":"...","parameters":{...}}},
    ...
  ]
}
```

返回 OpenAI ChatCompletion 格式。lea-agent 抽出 `tool_calls` 或最终 text，决定下一步。

耗时：**每轮 2-8s**（复杂 tool 调用 + 中文长上下文可到 8s+）

### 4b. kb_search tool 展开

**lea-agent → lea-rag**
```http
POST /kb/search
Host: lea-rag:8300
Content-Type: application/json

{
  "query": "Hadoop 生态圈",
  "kb_ids": [1],
  "top_k": 5,
  "tenant_id": "default",
  "user_id": "demo-user",
  "strategy": "hybrid"
}
```

lea-rag 内部：
1. 从 MySQL `retrieval_models` 查 embedding 模型配置
2. 中转 API 拿 query embedding
3. Milvus 混合检索（dense vector + BM25 sparse）
4. MySQL 补 chunk 元数据

**响应**（实测 top-3）
```json
[
  {
    "score": 0.846,
    "kb_id": "1",
    "document_id": "1",
    "chunk_type": "markdown",
    "source_filename": "bigdata_kb.md",
    "content": "## 2. Hadoop 生态圈核心组件\n\nHadoop 是大数据处理的基础框架...\n- HDFS（Hadoop Distributed File System）: 分布式文件系统..."
  },
  {"score": 0.825, ...},
  {"score": 0.798, ...}
]
```

耗时：**1-2s**（embedding 请求 + Milvus 查询 + DB 补数据）

### 4c. activate_skill tool 展开

**lea-agent 内存匹配**（不走网络）：从 init 阶段拿的 `cfg.Skills` 数组里按 `skill_name` 找到 `SkillDef`，直接把 `SystemPrompt` 作为 tool_result 返回给 LLM。

**同时异步**（`go t.prepareSandboxBundle`，不阻塞）：

```http
POST /sandbox/prepare-bundle
Host: lea-tools:8400
Content-Type: application/json

{
  "session_id": "debug-trace",
  "skill_id": "2",
  "skill_bundle_uri": "s3://lea-uploads/skills/xlsx/1.0.0.tar.gz",
  "tenant_id": "default",
  "user_id": "demo-user"
}
```

lea-tools 拉 MinIO 上的 bundle，解压后**推入沙箱 `/workspace/skill/`**（`Sandbox.write_file`）。为下一次 `code_interpreter` 调用做准备。

耗时：**主链路 <1ms（纯内存），异步链路 300-800ms（MinIO 下载 + 沙箱写入）**

### 4d. code_interpreter tool 展开

**首次调用触发建沙箱**：

```http
POST /sandbox/sessions
Host: lea-tools:8400
Content-Type: application/json

{
  "session_id": "debug-trace",
  "user_id": "demo-user",
  "tenant_id": "default"
}
```

lea-tools 内部：
```http
POST /sandboxes
Host: 10.192.241.137:3000  (Cube master)
Content-Type: application/json

{"templateID": "tpl-a58d55bcc1a54b5bbfbb4c00"}
```

Cube master 从 template 拉起一个 microVM，返回：
```json
{"sandboxID": "ec6403cb9b734605b317462b6e19331e", "domain": "cube.app", ...}
```

lea-tools 存起来 `session_id="debug-trace" → sandbox_id="ec6403cb..."` 映射，返回给 lea-agent。

**每次执行代码**：

```http
POST /sandbox/sessions/debug-trace/exec
Host: lea-tools:8400
Content-Type: application/json

{
  "code": "from openpyxl import Workbook\nwb = Workbook()\n...",
  "language": "python",
  "timeout": 180
}
```

lea-tools 内部路由到 Cube envd `:49999/execute`：

```http
POST https://10.192.241.137:443/execute
Host: 49999-ec6403cb9b734605b317462b6e19331e.cube.app  (Host header 路由)
Content-Type: application/json

{"code": "..."}
```

envd 在 microVM 内执行 python，NDJSON 流式返回 stdout/stderr。lea-tools 汇总后返给 lea-agent：

```json
{
  "stdout": "文件已保存到 /tmp/out.xlsx\n",
  "stderr": "",
  "exit_code": 0,
  "duration_ms": 2314
}
```

lea-agent 把这个包装成 `tool_result`，塞进 LLM history。

耗时：**建沙箱首次 800ms-2s，之后每次 exec 200ms-5s（看代码复杂度）**

---

## 阶段 5 · session_end · 记忆抽取入库

`run_turn` 结束后 lea-agent 拿到最终 `assistantText`，如果 `memory_mode != "off"`：

**lea-agent → lea-rag**（两步）

```http
POST /memory/extract
Host: lea-rag:8300

{
  "user_id": "demo-user",
  "tenant_id": "default",
  "agent_id": "2",
  "session_id": "debug-20260722-160730",
  "messages": [
    {"role":"user","content":"帮我生成 Hadoop 报表..."},
    {"role":"assistant","content":"已完成，文件保存到..."}
  ]
}
```

lea-rag 内部：
1. 前置过滤（消息数、字符数阈值）
2. 中转 API 调 qwen3-max 抽取偏好 → JSON 结构化
3. 每条 memory 生成 embedding → Milvus
4. MySQL `user_memories` 表写记录

**响应**
```json
{
  "extracted": 2,
  "memories": [
    {"id": 3, "content": "用户偏好...", "category": "preference", "importance": 7, "scope": "global"},
    {"id": 4, "content": "本次生成 Hadoop 报表...", "category": "progress", "importance": 6, "scope": "agent:2"}
  ]
}
```

再来一步合并画像：

```http
POST /memory/profile/merge
Host: lea-rag:8300
{"user_id":"demo-user","agent_id":"2","tenant_id":"default","session_id":"..."}
```

lea-rag 内部聚合当前 user 的所有 memory，生成 profile 文本存 `user_profiles` 表。

耗时：**extract 20-30s（LLM 抽取慢），merge 1-3s**

---

## 阶段 6 · 返回给用户

lea-agent 汇总 5 阶段的 trace + 最终 assistantText，返给调用方：

```json
{
  "output": "已完成 Hadoop 生态圈 xlsx 报表，保存到 /tmp/out.xlsx...",
  "trace": [
    {"phase":"init","action":"load_config","ms":14,"detail":{"tools":5,...}},
    {"phase":"pre_turn","action":"memory_inject","ms":892,"detail":{"injected":true,"chars":220}},
    {"phase":"pre_turn","action":"final_prompt","ms":0,"detail":{...}},
    {"phase":"run_turn","action":"completed","ms":75693,"detail":{"iterations":10,"tool_calls":[...]}},
    {"phase":"session_end","action":"memory_extract_merge","ms":2516,"detail":{"messages":2}}
  ]
}
```

如果走 `/api/chat/stream` (SSE)，则每个中间事件（tool_start / tool_end / kb_hit / skill_activated / sandbox_output / assistant_chunk）实时推给前端渲染。

---

## 阶段 7 · 取沙箱产物（可选）

用户在 xlsx 生成后要下载：

```http
GET /sandbox/sessions/debug-trace/download?path=/tmp/out.xlsx
Host: lea-tools:8400
```

lea-tools 走 Cube envd：

```http
GET https://10.192.241.137:443/files?path=/tmp/out.xlsx
Host: 49983-ec6403cb9b734605b317462b6e19331e.cube.app
```

envd 返回原始 bytes，lea-tools wrap 成：
```json
{"path": "/tmp/out.xlsx", "data_base64": "UEsDBBQAAAAIA..."}
```

前端 base64 解码，触发浏览器下载或渲染 artifact 面板。

---

## 全链路时序汇总（真实数据）

| 阶段 | 涉及服务 | 典型耗时 | 关键 IO |
|---|---|---|---|
| 前端选 agent | lea-server / MySQL | 20ms | GET /api/agents |
| init 加载配置 | lea-agent → lea-config → MySQL | 10-15ms | GET /internal/agent-config/{id} |
| pre_turn 记忆检索 | lea-agent → lea-rag → 中转 API + Milvus + MySQL | 700-900ms | POST /memory/search |
| run_turn 主循环 | lea-agent ↔ 中转 API + lea-rag + lea-tools + Cube | 15-80s | 多次 POST /v1/chat/completions + N 个 tool_call |
| session_end 抽取 | lea-agent → lea-rag → 中转 API + Milvus + MySQL | 20-30s | POST /memory/extract + /memory/profile/merge |
| 沙箱下载 | 前端 → lea-tools → Cube envd | 100-500ms | GET /sandbox/sessions/{id}/download |

---

## 一句话概括

**LLM 是"总调度"**——它决定何时查 KB、激活哪个 skill、跑什么代码；**Go agent 是 "tool proxy + 主循环管家"**——不主动执行业务，只把 tool_call 转成对 lea-rag / lea-tools / 中转 API 的 HTTP 调用；**skill 定义与 kb_ids 在 init 阶段一次性从 lea-config 拿全**，之后主链路不再回访 lea-config；**沙箱是有状态的**（session_id 复用），一次会话内多次 code_interpreter 共享同一个 microVM，直到 idle 10 分钟被回收。

---

## 关联源码

| 文件 | 作用 |
|---|---|
| `packages/lea-agent/internal/router/debug.go` | `/debug/trace` 端点，五阶段编排 |
| `packages/lea-agent/internal/runtime/config_loader.go` | 阶段 2，调 lea-config |
| `packages/lea-agent/internal/runtime/memory_middleware.go` | 阶段 3 + 阶段 5，调 lea-rag |
| `packages/lea-agent/internal/runtime/platform_tools.go` | 5 个 tool 装配 + activate_skill 内存匹配 |
| `packages/lea-agent/internal/runtime/code_interpreter.go` | code_interpreter tool，调 lea-tools |
| `packages/lea-agent/internal/runtime/kb_tools.go` | kb_search tool，调 lea-rag |
| `packages/lea-rag/src/lea_rag/server_kb.py` | `/kb/search` |
| `packages/lea-rag/src/lea_rag/server_memory.py` | `/memory/*` |
| `packages/lea-tools/src/lea_tools/api/sandbox.py` | `/sandbox/sessions/*` |
| `packages/lea-tools/src/lea_tools/services/_cube_sandbox.py` | envd 协议实现 |
| `packages/lea-config/src/lea_config/api/agents.py` | `/internal/agent-config/{id}` 聚合 |
