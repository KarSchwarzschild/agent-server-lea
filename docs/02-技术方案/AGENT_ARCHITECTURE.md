# Agent 架构与调用流程

配置期到运行期的完整视图。跟 `E2E_FLOW.md` 互补：那份讲**一次会话的时序**，这份讲**三类实体（Agent/Skill/Tool）如何组装 + 权限如何分层**。

---

## 1. 组件全景

```
┌──── 前端 ────┐
│ frontend-user│
└──────┬───────┘
       │  只连 lea-server
       ▼
┌────────────────────────────────┐
│  lea-server (BFF) :8000         │
│  ─────────────────────────       │
│  只做前端相关的事：              │
│  · auth（JWT/登录）              │
│  · chat 会话持久化（sessions 表） │
│  · SSE 长连接握手 + 事件中转     │
│  · attachment / artifact 上下行  │
│  · 用户视角只读聚合              │
│  · admin API（转发给 lea-config）│
└──────┬─────────────────────────┘
       │
       │ 内部调用（不对前端暴露）
       │
       ├──────────────┬──────────────┬────────────────┐
       ▼              ▼              ▼                 ▼
┌───────────┐  ┌───────────┐  ┌───────────┐   ┌───────────┐
│ lea-config │  │ lea-agent  │  │  lea-rag   │   │ lea-tools  │
│  :8500      │  │  :8100 Go  │  │  :8300     │   │  :8400     │
│─────────── │  │─────────── │  │─────────── │   │─────────── │
│Agent/Skill/│  │LLM 主循环  │  │KB / Memory │   │Tool 网关   │
│Tool 元数据  │  │+ tool 调度 │  │检索/抽取   │   │+ sandbox   │
│Admin 真源  │  │            │  │            │   │+ 权限限流   │
└─────┬──────┘  └──┬────────┘  └─────┬──────┘   └──────┬─────┘
      │            │                  │                  │
      │      直连  │──────────────────┘                  │
      │      lea-rag                                      │
      │            │                                      │
      │            │  code_interpreter                    │
      │            └─── 走网关 ────────────────────────────┘
      │
      ▼
   ┌──────────────┐        ┌──────────────┐
   │   MySQL       │        │   Milvus     │  向量库
   │ 202.205.160.9│        └──────────────┘
   └──────────────┘
                              ┌───────────────┐
                              │  Cube master  │  真 microVM
                              │ 10.192.241.137│
                              └───────────────┘
                              ┌──────────────┐
                              │  中转 API     │  LLM+embed
                              │  tenantapi-ai │
                              └──────────────┘

MinIO :9000 存 skill bundle（tar.gz），bundle_uri 写在 skills 表里。
```

---

## 2. 三类核心实体

### 2.1 Agent

存 `agents` 表。一个 agent 是"人设 + 配置"的集合：

| 字段 | 含义 | 例子 |
|---|---|---|
| name | 标识名 | `test` |
| soul_prompt | 人设与目标（**不写流程**） | "你是数据分析助手，擅长把技术资料整理成报表" |
| model | 用哪个 LLM | `qwen3-max` |
| max_turns | 单会话最多几轮 | 20 |
| max_tokens | 单次响应 token 上限 | 4096 |
| kb_mode | 知识库模式 | `auto` / `always` / `off` |
| memory_mode | 记忆模式 | `always` / `off` |
| enabled | 是否启用 | true |

**关键约定**：soul_prompt **只写"是谁 / 擅长什么"**，不写"先查 A 再调 B"。流程由 LLM 结合 tool schema 自己推理，避免 prompt 硬编码工具名（换名字/换实现要跟着改）。

### 2.2 Skill

存 `skills` 表 + MinIO bundle。一个 skill 是"给 LLM 看的指南 + 给沙箱用的代码"的双面体：

| 部分 | 存哪 | 谁看 |
|---|---|---|
| SKILL.md (system_prompt) | MySQL `skills.system_prompt` | LLM |
| bundle 脚本 (Python/JS) | MinIO `skills/<name>/<version>.tar.gz` | 沙箱内的代码 |

**LLM 视角**：一段 Markdown 说"要生成 xlsx 请按这样做：设置列宽 / 加粗表头 / 用 openpyxl.styles.Font"

**沙箱视角**：一堆脚本挂载在 `/workspace/skill/`，`code_interpreter` 生成的 python 可以 `import` 或 `subprocess` 调用

### 2.3 Tool

存 `tools` 表。一个 tool 是"LLM 可调用的原子能力"：

| 字段 | 含义 |
|---|---|
| name | LLM 看到的调用名 (`kb_search`, `code_interpreter`, `web_search` ...) |
| execution_type | `internal` / `api` / `sandbox` / `mcp` |
| schema_json | JSON Schema，转成 LLM 的 function-calling 参数 |
| endpoint | 内部服务路径或外部 URL |
| sandbox_config | execution_type=sandbox 时的模板/超时 |
| http_adapter_config | execution_type=api 时的 URL/method/mapping |

**Tool ≠ Skill**：
- Tool 是**原子操作**（一次 kb_search、一次 code exec）
- Skill 是**LLM 的方法论**（如何组合原子操作产出复杂产物）

### 2.4 三张 binding 表

`agent_kb_bindings` / `agent_skill_bindings` / `agent_tool_bindings` —— 决定"这个 agent 能用哪些 kb/skill/tool"。**能力发现的白名单在这里**。

### 2.5 权限表

`tool_permissions` —— tenant × role × agent × tool → allowed / rate_limit。**运行时校验的规则在这里**（只对走网关的 tool 生效）。

---

## 3. Admin 配置真源规则

**Agent / Skill / Tool / Team / Binding 的 admin CRUD 权威源在 `lea-config :8500`。**

历史上 `lea-server :8000` 里也有一份 `admin_*.py`（老 monolith 拆分遗留），跟 lea-config 指向同一张 MySQL 表，两边都能改。**新代码只调 lea-config**，`lea-server` 那份已标 `⚠️ DEPRECATED`。

| 领域 | 真源 | lea-server 老副本 | 状态 |
|---|---|---|---|
| Agent | lea-config `/admin/agents` | ⚠️ DEPRECATED | 迁移中 |
| Agent Bindings (skill/kb/tool) | lea-config `/admin/agents/{id}/skills\|kbs\|tools` | ⚠️ DEPRECATED | 迁移中 |
| Skill | lea-config `/admin/skills` | ⚠️ DEPRECATED | 迁移中 |
| Tool | lea-config `/admin/tools` | ⚠️ DEPRECATED | 迁移中 |
| Team | lea-config `/admin/teams` | ⚠️ DEPRECATED | 迁移中 |
| MCP Server | lea-server `/admin/mcp-servers` | — | lea-config 未来接管 |
| Model 配置 | lea-server `/admin/models` | — | lea-config 未来接管 |
| Knowledge Base | lea-rag `/kb/*` + lea-server `/admin/knowledge` | — | 真源本就在 lea-rag |
| Memory | lea-rag `/memory/*` + lea-server `/admin/memories` | — | 真源本就在 lea-rag |
| User | lea-server `/admin/users` | — | 用户身份不属于 agent 配置，保留合理 |

**迁移三步走**：
- **Phase 1（现在完成）**：文档定原则 + lea-server 副本加 `⚠️ DEPRECATED` 注释
- **Phase 2**：lea-server 副本改成 `httpx` 透传到 lea-config（不再直接查 DB）
- **Phase 3**：前端要么直连 lea-config，要么保留 BFF 转发（BFF 转发推荐，因为需要 auth/CORS/rate limit）

---

## 4. 配置期 · admin 建 agent 全流程

（以下 API 均以 lea-config 真源路径为准）

```
1. 建 tool（platform 层，全 tenant 共享）
   POST :8500/admin/tools {name:"code_interpreter", execution_type:"sandbox", ...}
   或者启动时 seed（packages/lea-tools/src/lea_tools/seed.py BUILTIN_TOOLS）

2. 建 skill（platform 层）
   POST :8500/admin/skills 上传 SKILL.md
   POST :8500/admin/skills/{id}/bundle 上传 .tar.gz → MinIO

3. 建 knowledge base（tenant 层）
   POST /kb/create + POST /kb/{id}/upload 上传文档

4. 建 agent（tenant 层）
   POST /admin/agents {name, soul_prompt, model, ...}

5. 三张 binding 授权
   POST /admin/agents/{id}/kbs      {kb_id: 1}
   POST /admin/agents/{id}/skills   {skill_id: 2}
   POST /admin/agents/{id}/tools    {tool_id: 5}

6. 权限规则（可选）
   默认无规则=allow；要限制时：
   POST /admin/permissions {tool_id, tenant_id, role, allowed:false}
   或指定 rate_limit
```

Agent 建好后，`GET :8500/internal/agent-config/{id}` 就能拿到一份聚合视图，供 lea-agent 使用。

---

## 5. 运行期 · 一次会话的完整链路

用户从选 agent 到收到最终文本，涉及的服务和 IO：

### 阶段 0 · 前端选 agent

```
用户 → GET :8000/api/agents
       → lea-server 查 MySQL agents 表（filter by tenant + enabled）
       ← {items: [{id, name, welcome_message, avatar, ...}]}
```

用户点选 → 前端记住 `agent_id`，进入对话。

### 阶段 1 · 发消息（生产链路）

前端两步：先创会话，再 POST 消息 + GET 流事件。lea-server 做鉴权 + 会话 DB 持久化，把请求代理到 lea-agent。

```
1) 前端 → POST :8000/api/chat/sessions           创会话
          {agent_id, title?}
          ← {session_id}

2) 前端 → POST :8000/api/chat/send               启动一 turn（同步返回，异步跑）
          {session_id, message}
          ← {status:"queued"}
   |
   |  内部转发：
   |  lea-server → POST :8100/api/chat/send  {session_id, agent_id, message}
   |               lea-agent 拿到后：
   |                 - session.Manager 查/建 turn
   |                 - runtime.LoadAgentConfig（阶段 2 起）
   |                 - engine.RunTurn（阶段 4）
   |                 - 事件 push 到 in-mem event bus
   |
3) 前端 → GET :8000/api/chat/stream/{session_id}  SSE 长连接消费事件
          ← event: tool_start / kb_hit / skill_activated /
                   sandbox_output / assistant_chunk / assistant_done ...
```

调试用（绕过 lea-server + 无 SSE，一次性返回全 trace）：

```
POST :8100/debug/trace {agent_id, user_id, message}
      ← {output, trace: [5 阶段耗时+detail]}
```

**agent 主循环只有 Go lea-agent 一条路径**，无 LangGraph fallback（历史 `lea_graph` 包不再使用）。生产 `/chat/send` 和调试 `/debug/trace` 走的是**同一套内部代码**（LoadAgentConfig → memory pre_turn → engine.RunTurn → memory session_end），差异只在响应形式（SSE 事件流 vs 一次性 JSON）。

### 阶段 2 · init 拉全配置

```
lea-agent → GET :8500/internal/agent-config/{agent_id}?tenant_id=...

lea-config 内部（MySQL join）：
  SELECT agents
  LEFT JOIN agent_kb_bindings
  LEFT JOIN agent_skill_bindings LEFT JOIN skills（含 system_prompt + bundle_uri）
  LEFT JOIN agent_tool_bindings LEFT JOIN tools（含 schema_json）
  WHERE agent_id=? AND enabled=true

返回：
{
  agent: {model, soul_prompt, max_turns, memory_mode, ...},
  kb_ids: [1],
  skills: [
    {id, name:"xlsx", system_prompt:"<完整 SKILL.md>",
     bundle_uri:"s3://lea-uploads/skills/xlsx/1.0.0.tar.gz"},
    ...
  ],
  tools: [
    {name:"web_search", description, input_schema, ...}   // dynamic tools
  ],
  llm_base_url, llm_api_key
}
```

**关键点**：
- skill 数组已过滤（只此 agent 绑定的）→ LLM 看不到无权限的
- kb_ids 已过滤 → kb_search 只在此列表内检索
- tools 是**动态工具**（第三方 API / MCP），内置 5 个 tool（kb_search/memory_*/activate_skill/code_interpreter）在 agent 侧硬编码装配

耗时：~15ms

### 阶段 3 · pre_turn 检索记忆

memory_mode ≠ "off" 时执行：

```
lea-agent → POST :8300/memory/search
            {user_id, tenant_id, agent_id, query}

lea-rag 内部：
  中转 API embedding(query)
  Milvus 向量检索 user_memories（filter by user_id + scope）
  MySQL 补 metadata

返回：[{content, category, importance, scope}, ...]

lea-agent 拼装：
  system_prompt = "[相关记忆]\n- ...\n- ...\n\n" + agent.soul_prompt
```

首次会话 / 新用户 → 返回空，无注入。

耗时：~800ms（主要是 embed）

### 阶段 4 · run_turn LLM 主循环

lea-agent 装配 5 个内置 tool + 动态 tool，交给 engine：

```
内置 tool（硬编码在 platform_tools.go）：
  - kb_search       → 直连 :8300/kb/search
  - memory_search   → 直连 :8300/memory/search
  - memory_add      → 直连 :8300/memory/add
  - activate_skill  → 内存查 cfg.Skills
  - code_interpreter→ 走 :8400/tools/execute（网关）

动态 tool（cfg.Tools）：全部走 :8400/tools/execute
```

LLM 循环：

```
Iteration N：
  lea-agent → POST 中转 API /v1/chat/completions
              {model, messages(含 tool history), tools(schema 列表)}
  ← {choices[0].message: {content 或 tool_calls}}

  若 tool_calls：
    for each tool_call：
      按 tool_name 路由到对应 handler
      拿到 result → 存入 messages（role:"tool"）
    继续下一 iteration

  若 content 非空且无 tool_calls：
    终止循环，assistantText = content
```

每 iteration 耗时 2-8s。总循环 15-80s。

### 阶段 5 · session_end 抽取记忆

```
lea-agent → POST :8300/memory/extract
            {user_id, tenant_id, agent_id, session_id, messages}

lea-rag 内部：
  前置过滤（user_msgs < 1 或 total_chars < 30 → skip）
  中转 LLM 抽取 → 结构化 JSON
  每条 memory → embedding → Milvus
  MySQL user_memories 写入

返回：{extracted: N, memories: [...]}

lea-agent → POST :8300/memory/profile/merge  合并画像
```

耗时：20-30s（LLM 抽取慢）

### 阶段 6 · 返回

```
lea-agent 汇总 → {output, trace: [5 阶段耗时+detail]}

SSE 版本：每个中间事件（tool_start / kb_hit / skill_activated / sandbox_output / assistant_chunk）实时推给前端
```

---

## 6. Tool 路由 · 两条路径

### 5.1 直连路径（4 个内置 tool）

```
LLM tool_call → agent.Tool.Run() → HTTP 直达下游

  kb_search       → :8300/kb/search
  memory_search   → :8300/memory/search
  memory_add      → :8300/memory/add
  activate_skill  → 内存匹配 cfg.Skills（不走网络）
```

**为啥直连**：读操作 DB 层已按 tenant/user 隔离；memory_add 写自己空间副作用局部；activate_skill 白名单在 lea-config 层已过滤。加网关只会加延迟，不加价值。

### 5.2 网关路径（code_interpreter + 动态 tool）

```
LLM tool_call → agent.ExecuteRemoteTool() → HTTP → :8400/tools/execute

  Headers:
    X-Tenant-ID: <tenant>
    X-User-Role: user|admin|*
    X-Agent-ID: <id>
  Body:
    {tool_name, params, session_id}

lea-tools 依次检查：
  1. check_permission(tenant, role, agent, tool_name)
     查 tool_permissions 表
     - 默认 allow（无规则时）
     - 匹配到 deny → 403 "tool not allowed"
  2. check_rate_limit(tenant, tool_name)
     Redis INCR → 超阈值 → 429 "rate limit exceeded"
  3. get_credential_for_tool()  若 tool 需要外部 API key
  4. router.execute() 按 execution_type 分派：
     sandbox → SandboxSessionManager (复用 Cube microVM)
     api     → HTTP adapter (第三方 API)
     internal→ 内部服务代理
     mcp     → MCP proxy

返回 {status, result, error, usage}
```

**为啥走网关**：`code_interpreter` 跑任意代码（有外部副作用+占 microVM 资源）；动态 tool 大多是第三方 API（有 quota+费用）。都需要集中控制。

### 5.3 分档决策原则

**判断新 tool 该走哪条**——问一句 "这个操作的副作用只影响调用者自己吗？"

| 情况 | 路径 |
|---|---|
| 读自己/共享数据（KB、Memory 读） | 直连 |
| 写自己私有空间（memory_add） | 直连 |
| 元操作（activate_skill，白名单在 config 层过滤） | 直连 |
| **执行任意代码 / 占资源（code_interpreter）** | **走网关** |
| 消耗共享资源 / 有外部 side effect / 需要 quota | 走网关 |
| MCP 动态工具（用户 own 代码） | 全部走网关 |

---

## 7. Skill 完整生命周期

```
Admin 上传 skill bundle
    POST /admin/skills/{id}/bundle
    → 上传 tar.gz 到 MinIO skills bucket
    → bundle_uri 存入 skills 表

Admin 绑定 agent
    POST /admin/agents/{id}/skills {skill_id}
    → 插入 agent_skill_bindings

------- 分界线：以上是配置期 -------

用户发消息 → agent init 拉配置
    lea-config 返回 skills 数组（含完整 SKILL.md + bundle_uri）
    lea-agent 存到内存 cfg.Skills

Agent 构造 system prompt
    system_prompt = soul_prompt + "\n\n## 可用技能\n- xlsx: <描述>\n- pptx: <描述>"

LLM 决定用某个 skill → tool_call activate_skill("xlsx")
    主链路（同步）：
      agent 内存匹配 cfg.Skills.name == "xlsx"
      返回完整 SKILL.md 作为 tool_result
      LLM 下一轮 iteration 看到具体规范

    异步（不阻塞）：
      go prepareSandboxBundle()
      → POST :8400/sandbox/prepare-bundle {session_id, skill_id, bundle_uri}
      lea-tools 从 MinIO 拉 bundle → envd 上传到沙箱 /workspace/skill/

LLM 依据 SKILL.md 生成 python 代码
    可以 import "/workspace/skill/lib/..." 使用 bundle 里的脚本

调 code_interpreter 执行
    走 gateway → sandbox → 生成产物
```

**关键点**：SKILL.md 和 bundle 脚本**分离但配对**——前者给 LLM 看，后者给运行时用。分开的好处：改 LLM 指导不用重打包代码，反之亦然。

---

## 8. Sandbox 生命周期

```
LLM 首次 code_interpreter → agent.ensureSandbox()
    lea-agent → POST :8400/sandbox/sessions
                {session_id, user_id, tenant_id}
    lea-tools 内部：
      POST Cube :3000/sandboxes {templateID: "tpl-a58d..."}
      ← {sandboxID: "ec6403cb..."}
      存 map: session_id → sandbox_id

后续 code_interpreter → agent.execCode()
    lea-agent → POST :8400/tools/execute
                {tool_name:"code_interpreter",
                 params:{code, session_id}}
    gateway 校验 → _execute_sandbox()
      → SandboxSessionManager.execute(session_id, code)
      → HTTPS Cube :443/execute
        Host: 49999-<sandbox_id>.cube.app     ← exec 用 49999
        {code}
      ← {stdout, stderr, exit_code}

取沙箱产物 → 用户 GET :8400/sandbox/sessions/{sid}/download?path=/tmp/x.xlsx
    lea-tools → HTTPS Cube :443/files?path=/tmp/x.xlsx
                Host: 49983-<sandbox_id>.cube.app   ← files 用 49983
                ← raw bytes
                wrap 成 base64 返给用户

销毁：目前无主动销毁，靠 lea-tools 内 IDLE_TIMEOUT=600s + Cube 服务端超时
     agent 侧 Destroy() 定义了但未接线（技术债）
```

**envd 端口约定**：`:49999` 处理 `POST /execute`（跑代码），`:49983` 处理 `GET|POST /files`（读写文件）+ `GET /health`。**同一个 microVM 两个端口**，用 `Host: <port>-<sandboxID>.cube.app` header 路由。

---

## 9. 权限模型 · 三层

从粗到细：

**Layer 1 · 能力发现（配置层白名单）**
- 表：`agent_kb_bindings` / `agent_skill_bindings` / `agent_tool_bindings`
- 效果：agent 根本看不到没绑的 skill/tool/kb，LLM 无从调用
- 时机：init 阶段拉 config 时通过 SQL join 完成过滤
- 特点：**零运行时成本**，安全性高

**Layer 2 · 调用校验（gateway 层）**
- 表：`tool_permissions`（tenant × role × agent → tool 允许/限流）
- 效果：即使 agent 绑了这个 tool，如果 tenant/role 被 deny 也会 403
- 时机：每次 `POST :8400/tools/execute` 时
- 特点：**每次都过 DB**（未来加 LRU cache），支持热更新（改一行 DB 立即生效）
- 目前只对 `code_interpreter` 生效（直连 tool 绕过）

**Layer 3 · 数据隔离（DB/存储层）**
- 各表 `tenant_id` / `user_id` 列 + Milvus scope filter
- 效果：读取时天然按调用者身份过滤，SQL 写错也拿不到跨租户数据
- 时机：每次 SELECT / vector search
- 特点：**最终防线**，即使 Layer 1/2 都错也保数据不外泄

---

## 10. 配置期 vs 运行期速查

| 项 | 配置期 | 运行期 |
|---|---|---|
| agent 参数 | admin UI 建 → agents 表 | lea-config `/internal/agent-config` 聚合返回 |
| kb 归属 | admin 建 kb → 绑 agent | init 时 `kb_ids` 已过滤 |
| skill 归属 | admin 上传 → 绑 agent | init 时 `skills` 数组已过滤 |
| tool 归属 | admin seed / 建 → 绑 agent | init 时 dynamic tools 已过滤，内置 tool 硬编码 |
| **权限规则** | admin 写 tool_permissions | 走 gateway 时每次校验 |
| skill bundle | MinIO 存 tar.gz | activate_skill 触发异步下载到沙箱 |
| sandbox 实例 | 无 | 首次 code_interpreter 建，10min idle 后自动回收 |

---

## 11. 关联源码索引

**lea-agent（Go）**
- `internal/router/debug.go` — `/debug/trace` 端点，五阶段编排
- `internal/runtime/config_loader.go` — 阶段 2，调 lea-config
- `internal/runtime/memory_middleware.go` — 阶段 3/5，调 lea-rag
- `internal/runtime/platform_tools.go` — 5 个内置 tool 装配
- `internal/runtime/code_interpreter.go` — code_interpreter tool，走 gateway
- `internal/runtime/kb_tools.go` — kb_search 直连
- `internal/runtime/gateway.go` — `ExecuteRemoteTool`，唯一网关调用入口

**lea-config（Python）**
- `api/agents.py` — agent CRUD (`/admin/agents/*`)
- `api/bindings.py` — kb/skill/tool 绑定
- `api/internal.py` — `/internal/agent-config/{id}` 聚合

**lea-rag（Python）**
- `server_kb.py` — `/kb/search` 混合检索
- `server_memory.py` — `/memory/search` `/memory/add` `/memory/extract` `/memory/profile/merge`
- `memory/extractor.py` — LLM 抽取逻辑
- `memory/config.py` — `extract_min_messages` 等阈值

**lea-tools（Python）**
- `api/execute.py` — `/tools/execute` 网关入口
- `services/__init__.py::check_permission` — 权限校验
- `services/cache.py::check_rate_limit` — Redis 限流
- `executor/router.py::_execute_sandbox` — sandbox 分派（session 复用）
- `api/sandbox.py` — `/sandbox/sessions/*` 会话 CRUD
- `services/_cube_sandbox.py` — Cube envd 协议实现
- `seed.py::BUILTIN_TOOLS` — 5 个内置 tool seed 定义

---

## 一句话概括

**Agent 是配置的集合，LLM 是运行的引擎，Tool 是能力的入口**。配置期用三张 binding 表和 tool_permissions 决定"这个 agent 能干啥、在什么限制下能干"，运行期 lea-agent 把配置装成 LLM 可见的 tool schema，LLM 按需 tool_call，agent 按 blast radius 分派：读/自我写直连、执行/共享写走网关加权限层。所有权限规则都在 DB 中可见（不散落在 if-else 里），改一条记录即刻生效。
