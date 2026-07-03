# LEA Platform API 合同（前端契约）

> **定位**：本文件是 **lea-platform 后端** 与 **lifelong-edu 前端** 之间的正式接口合同。
> **原则**：前端为准 —— 我们在 lea-platform 上按此合同重新实现，不修改前端。
> **来源**：`D:\gitlab\lifelong-edu-agent-feature-super-agent-enhance` (frontend-user + backend, ground-truth 交叉验证于 2026-07-03)

---

## 元信息

- **合同版本**：v2（对应 SSE `protocol_version: 2`）
- **认证**：JWT Bearer（HTTP header）+ query string `?token=`（SSE 专用，因 EventSource 不支持自定义 header）
- **传输**：async POST → 202 → EventSource GET，支持 `Last-Event-Id` / `?last_event_id=` 续传
- **API 前缀**：`/api/*`
- **响应格式**：JSON（标准），SSE `text/event-stream`（流式）
- **JWT payload**：`{sub:user_id, roles:string[], permissions:string[], exp}`，HS256，无 refresh token
- **SSE 事件框架**：`id: <ms-ts>-<worker8hex>-<n>\nevent: <name>\ndata: <json + _sid injected>\n\n`
- **SSE 缓存 & 重放**：Redis Stream `sse:events:{sid}` (XADD maxlen=500, TTL=30min)，无 Redis 时内存 LRU 200 sessions

---

## Phase 1 优先级（M1 里程碑：主聊天 + 附件 + KB + 引用溯源）

> **Wow 项**：**references 引用溯源**（KB 检索附来源，教育刚需 / to B 合规刚需）
> **不做**：plan-mode / parallel / debate / classroom / artifact / admin panel（全部推到 Ph2+）

### ✅ Must-Have（Ph1 必跑通 · 13 endpoints）

**认证（3）**
1. `POST /api/auth/login` — [Auth 整块搬 core/auth.py]
2. `GET  /api/auth/me` — 用户信息
3. `POST /api/auth/register` — 注册（管理员批量创建可先手动）

**会话 CRUD（4）**
4. `GET  /api/user/super-agent/sessions` — 侧栏会话列表
5. `POST /api/user/super-agent/sessions` — 新建会话
6. `GET  /api/user/super-agent/sessions/{sid}` — 会话详情（含富化 messages）
7. `POST /api/user/super-agent/sessions/{sid}/stop` — 取消

**对话主流（2）**
8. `POST /api/user/super-agent/chat/stream?async=true` → 202
9. `GET  /api/user/super-agent/chat/events/{sid}?token=&last_event_id=` — SSE + Redis Stream 重放

**Skill/附件/KB（4）**
10. `GET  /api/user/super-agent/skills` — Skill 列表（agent 配置需要）
11. `POST /api/user/super-agent/attachments/upload` — 附件上传
12. `POST /api/user/super-agent/attachments/import-from-kb` — 从 KB 引入
13. `GET  /api/user/knowledge-bases/documents/{doc_id}` — KB 文档轮询状态

### ✅ Must-Have SSE 事件（14 个）

| 事件 | 用途 |
|---|---|
| `session_created` | 建立 stream 首个事件 |
| `heartbeat` | 15s 空闲心跳 |
| `thinking` | Agent 思考过程 |
| `content` | LLM token 流式 |
| `tool_call_start` | 工具调用开始 |
| `tool_call_delta` | 工具入参增量 |
| `observation` | 工具返回结果（简化 tool_result 用这个） |
| `references` | **Wow 项：KB/工具引用溯源** |
| `llm_usage` | Token 计量（同时落 token_usage_log 表） |
| `pageindex_status` | 附件索引构建状态 |
| `cancelled` | 用户中止 |
| `done` | 整轮完成 |
| `error` | 异常 |
| `intent` | 意图路由（可空，先跑通即可） |

### 🟡 Nice-to-Have（Ph1 后段 / 有余力补）
- `POST /chat/regenerate` — 重新生成
- `POST /chat/edit` — 编辑重发
- `POST /chat/switch-version` — 版本切换（tree node pool）
- `DELETE /sessions/{sid}` — 软删
- `GET  /files/*` — 生成文件浏览下载
- SSE `intent_routing`, `routing`, `query_rewrite` — 路由 UI 增强

### ⏳ Later（Phase 2+）
- 所有 `/api/admin/*`（214 routes）→ Ph2 分批
- `POST /tools/result` + `client_tool` 回桥 → Ph3
- `plan_*` 事件族 + `POST /plans/{id}/approve|reject` → Ph3
- `parallel_*` / `peer_review` / `diagnostic_*` → Ph3
- `debate_*` + `/api/user/debate/*` → Ph3
- `artifact_*` + `POST /outline/confirm` → Ph3
- `classroom_*` → Ph4（特色 Skill 触发时才需）
- `sub_agent_*` 事件族 → Ph2（LangGraph subgraphs 天然支持）
- `memory_context` + 长期记忆 → Ph2（LangGraph.store）

---

## Section 1 — Auth

Router prefix: `/api/auth`. 后端: `backend/app/api/common/auth.py`. JWT 库: `python-jose` (HS256)。

| Method | Path | Body → Response | 前端 | 后端 |
|---|---|---|---|---|
| POST | `/api/auth/login` | `{username, password}` → `{access_token, token_type:"bearer"}` | `api/auth.ts:22` | `common/auth.py:54` |
| POST | `/api/auth/register` | `{username, password, email?}` → `UserResponse` | `auth.ts:27` | `common/auth.py:102` |
| GET | `/api/auth/me` | — → `UserResponse` | `auth.ts:32` | `common/auth.py:139` |
| POST | `/api/auth/change-password` | `{old_password, new_password}` → `{message}` | Profile 页 | `common/auth.py:152` |
| POST | `/api/auth/logout` | — → `{message}` | — | `common/auth.py:180` |

```ts
UserResponse = { user_id, username, email?, avatar?, roles: string[], permissions: string[] }
// ⚠️ 前端 UserInfo 用 role: string，后端返 roles: string[]（loose typing）
```

**Refresh token**：无。401 → 强制重登（axios 拦截器）。SSE 端点用 `?token=` 走 query（`common/auth.py:113 get_user_from_token_str`）。

---

## Section 2 — Chat streaming (POST /chat/stream + GET /chat/events)

Router: `/api/user/super-agent`. 后端: `backend/app/api/user/super_agent.py`。

### POST `/api/user/super-agent/chat/stream?async=true`（`super_agent.py:376`）

请求体 `ChatRequest` (`super_agent.py:50`):
```ts
{
  message: string;                    // ≤10000; 空只允许在有 attachment_ids 时
  session_id?: string;                // 缺省服务端生成 `sa_<12hex>` 并同步 INSERT DB
  use_llm_intent?: boolean;
  force_skill?: string;               // '__debate__' → debate; '__qa__' → 跳过 skill; else skill.name
  mode?: 'plan';
  discussion_mode?: 'debate'|'seminar'|'socratic';
  attachment_ids?: string[];
  search_depth?: 'quick'|'balanced'|'deep';
  agent_id?: number;
  protocol_version?: 1|2;             // 前端固定发 2
  classroom_config?: { quality_mode?, language?, voice_preset?, theme_id?, ... };
}
```

**响应**：HTTP **202** `{session_id, message: "chat started"}`。
副作用：预建 DB 会话行 → 起 `asyncio.Task` 运行 `orchestrator.chat_stream()` → 每个 SSE 事件推入 `SSEBroker` (`sse:events:{sid}` Redis Stream)。

### GET `/api/user/super-agent/chat/events/{sid}?token=<jwt>&last_event_id=<id>`（`super_agent.py:651`）

- Auth: 优先 `Authorization` header，回退 `?token=`。也接受 `Last-Event-ID` HTTP header。
- Ownership: session.user_id 必须匹配 token 用户。
- 行为：
  1. `subscribe(sid)` 拿到活 queue
  2. 从 `last_event_id` 之后重放缓存（无 id 则全放）
  3. 消费实时 queue，去重 vs. 重放集
  4. `done`/`error` 或 1500s 超时终止
  5. 空闲 15s 一个 `heartbeat`
- 前端用 `EventSource`（`SuperAgent.vue:2002`），`last_event_id` 存 `sessionStorage`，指数退避重连 (`SuperAgent.vue:1405`)。

**关键事件缓存策略** (`core/sse_broker.py:54`)：`done, error, plan_created, plan_submitted, plan_approved, plan_rejected, session_error, session_stopped, cancelled, artifact_create, artifact_done, client_tool, intent_confirm, outline_preview` — 5s awaited gather；其余 `put_nowait`（256 队列满则丢弃 non-critical）。

**取消**：`POST /sessions/{sid}/stop`（`:764`）→ `DistributedCancelToken`（Redis pub/sub）。

---

## Section 3 — 完整 SSE 事件目录（85 个）

### 3.1 主循环

| 事件 | 发送位置 | data | Critical |
|---|---|---|---|
| `session_created` | `orchestrator.py:619` | `{session_id, title, protocol_version}` | — |
| `memory_context` | `orchestrator.py:645` | `{memories: string[], count}` | — |
| `thinking` | `skill_handler.py:630`, `forked_subagent.py:960,1078` | `{text, step, timestamp?, is_streaming?}` | — |
| `intent_routing` | `orchestrator.py:946` | `{status:'analyzing'}` | — |
| `intent` | `orchestrator.py:889,1096,1271,1358` | `{type:'skill'|'debate'|'qa'|'context_qa', skill_name?, confidence, ...}` | — |
| `intent_confirm` | `orchestrator.py:1335` | `{candidates:[{skill_name,display_name,description,confidence}], question}` | ✅ |
| `mode_selection_required` | orchestrator/analyze_plan | `{modes:[...]}` | — |
| `routing` | `orchestrator.py:1381` | `{tier, model, score, reason, mode, mode_reason}` | — |
| `query_rewrite` | `orchestrator.py:1406` | `{original, rewritten}` | — |
| `content` | `orchestrator.py:743`, `skill_handler` content_handler, `debate_handler.py:125`, `forked_subagent.py:534` | `{text, stage?}` | — |
| `answering` | `forked_subagent.py:500,703,774` | `{message}` | — |
| `references` | `orchestrator.py:745` | `{references:[{title,url,...}], mode:'append'|'replace'}` | — |
| `llm_usage` | `skill_handler.py:672`, `forked_subagent` | `{model, prompt_tokens, completion_tokens, total_tokens}` — **同时写 token_usage_log 表** | — |
| `iteration_start` | `forked_subagent.py:946` | `{step, model}` | — |
| `reasoning_chunk` / `reasoning_done` | `forked_subagent.py:528,758,531,540,687` | `{chunk}` / `{reasoning}` | — |
| `phase` | `forked_subagent.py:1332` | `{phase:'writing', status:'start'}` | — |
| `error` | multiple | `{message, code?}` | ✅ |
| `cancelled` | `orchestrator.py`, `skill_handler.py:605,704`, `forked_subagent.py:940` | `{reason:'user_stopped'}` | ✅ |
| `done` | `orchestrator.py:1344,491,510`, `forked_subagent.py:1384` | `{session_id}` | ✅ |
| `heartbeat` | `sse_broker`/`super_agent.py:747` | `{ts}` | — |

### 3.2 Tool calls (main agent)

| 事件 | data |
|---|---|
| `action` | `{tool, tool_display_name?, input, tool_call_id, ...}` legacy |
| `observation` | `{tool, tool_call_id, result, success, execution_time_ms}` legacy |
| `tool_call_start` | `{tool_call_id, tool_name, tool_display_name, tool_input?}` |
| `tool_call_delta` | `{tool_call_id, arguments_delta}` |

⚠️ 没有独立 `tool_result` 事件 —— 结果通过 `observation`（legacy）或后续 `content` 传递。

### 3.3 Sub-agent（透传）

11 个：`sub_agent_start / _thinking / _action / _observation / _content / _answering / _tool_call_start / _tool_call_delta / _complete / _error / subagent_batch_start / _done`

### 3.4 Skill 生命周期

`skill_start / skill_done / skill_state / verification / next_skill_suggestions`

### 3.5 Plan mode（全 Critical）

`plan_created / plan_submitted / plan_approved / plan_rejected / plan_step / plan_step_content_reset / plan_completed / analyze_plan / outline_preview`

### 3.6 Parallel 分解 / 质量

`parallel_decompose / parallel_progress / parallel_subtask_start / _progress / _content / _content_reset / _tool / _scope_retry / _done / parallel_synthesizing / parallel_synthesis_delta / parallel_diagnosing / diagnostic_escalation / diagnostic_report / peer_review / data_source_progress`

### 3.7 附件 / PageIndex

`pageindex_status {attachment_id, status:'building'|'ready'|'failed'|'not_supported'} / suggestions {queries[]}`

### 3.8 聚合@N

`aggregation_progress / synthesis_content`

### 3.9 Debate

`debate_created / round_start / round_end / round_summary / expert_intro / speech / speech_chunk / final_summary / debate_done / status`

### 3.10 Client-tool 回桥（Critical）

`client_tool {tool_use_id, tool_call_id, tool_name, tool_label, tool_input, client_tool_type?, standalone_plugin?:{...}}`

### 3.11 Artifact（create/done Critical）

`artifact_create / artifact_delta / artifact_done / artifact_error`

### 3.12 Classroom（Skill 输出时才有）

`classroom_outline_ready / classroom_scene_generated / classroom_scene_audio_ready / classroom_scene_failed / classroom_ready / classroom_scene_change / classroom_action_start / _done / classroom_status_change / classroom_discussion_start / _end`

---

## Section 4 — Session / Message CRUD

Router: `/api/user/super-agent`。

| Method | Path | Body | Response | 主流阻塞? |
|---|---|---|---|---|
| GET | `/sessions?status=&agent_id=&limit=&offset=` (`:1289`) | — | `SessionInfo[]` | No |
| POST | `/sessions` (`:1359`) | `{title?}` | `{session_id, title}` | No |
| GET | `/sessions/{sid}` (`:1393`) | — | `{session_id, title, status, messages[富化: siblings+attachments+artifact_id], context:{last_skill_status,revision}, created_at, updated_at}` | ✅ 恢复时 |
| DELETE | `/sessions/{sid}` (`:1488`) | — | `{message, session_id}` (软删 status='archived') | No |
| POST | `/sessions/{sid}/stop` (`:764`) | — | `{success, session_id}` | ✅ |
| POST | `/chat/regenerate?async=true` (`:824`) | `{session_id, message_id, force_skill?, agent_id?, revision, mode?}` | 202 `{session_id, new_message_id, revision}` | ✅ |
| POST | `/chat/edit?async=true` (`:966`) | `{session_id, message_id, new_content, force_skill?, agent_id?, revision, mode?}` | 202 | ✅ |
| POST | `/chat/switch-version` (`:1096`) | `{session_id, target_message_id, revision}` | `{session_id, messages[], revision}` | No |
| POST | `/outline/confirm` (`:1185`) | `{session_id, outline}` | `{status:"confirmed"}` | ✅ |
| POST | `/share/create` (`:2095`) | `{session_id, message_index}` | `{share_url, expires_at}` | No |

**Revision（乐观锁）**：`SuperAgentSession.context.revision` 计数器；`RevisionConflictError` → HTTP 409。

**⚠️ 关键**：`GET /sessions/{sid}` 的 messages 是**富化**过的（siblings/attachments/artifact_id），不是 SQL raw。

---

## Section 5 — Files, Attachments, PageIndex

### Attachments（聊天上传）
- **POST `/attachments/upload`** (`:157`) — single-file multipart, ≤20MB, docs `pdf/docx/xlsx/pptx/txt/md/csv`, images `jpg/jpeg/png/gif/webp`。返回 `AttachmentResponse {id, filename, file_category, file_ext, size, status, index_status}`。前端 XHR 进度 (`api/superagent.ts:593`)。
- **POST `/attachments/import-from-kb`** (`:216`) — `{doc_ids:string[] (1-10), session_id?}` → `AttachmentResponse[]`。

### PageIndex（异步 PDF 树索引）
Fire-and-forget bg task，通过 chat_stream 开头 `pageindex_status` SSE 内联汇报（`orchestrator.py:668-677`）。无独立 REST poll。状态映射：pending/processing→`building` / ready→`ready` / failed→`failed` / unknown ext→`not_supported`。

### 生成文件

| Method | Path | Response |
|---|---|---|
| GET | `/files?limit&offset` (`:1542`) | `FileInfoV2[] {id,filename,size,file_type,created_at,download_count}` |
| GET | `/files/{fid}/download` | Binary |
| DELETE | `/files/{fid}` | 软删 |
| GET | `/files/{fid}/content` | `{content, encoding}` ≤5MB |
| POST | `/files/{fid}/convert/pdf` | `{success, pdf_file, conversion_time_ms}` |
| POST | `/files/{fid}/preview-token` | `{token, preview_url, expires_in:600}` |
| GET | `/files/preview/{token}/{filename}` | Binary (无 JWT) |
| GET | `/sandbox/files/{sandbox_id}/{filename}` | Binary |

### KB 文档
- POST `/api/user/knowledge-bases/documents` — 上传
- GET `/api/user/knowledge-bases/documents/{doc_id}` — **轮询 upload_status/chunk_status**
- DELETE + `/retry-chunk`

---

## Section 6 — Client-tool 回桥

后端发 `client_tool` SSE (Critical) → 调 `ClientToolManager.register(tool_use_id, ...)` 拿 `asyncio.Future` → `await asyncio.wait_for(future, timeout)`。

**发射位置**：`subagent_tool_executor.py:334`, `utils/subagent_tool_executor.py:344`, `plan_mode.py:515`。

**回调端点**：**POST `/api/user/super-agent/tools/result`** (`:1141`):
```ts
Request: { tool_use_id: string, content: string, is_error?: boolean }
Response: { message: "tool result received" }
// 404 if tool_use_id unknown/expired
```

**超时**：default 300s；`standalone_plugin` 60s。超时 observation = `[客户端工具超时]...` 继续。前端另有 60s 交互倒计时。

**客户端工具类型**：`confirm/client_confirm`, `user_select/client_select`, `text_input/client_input`, `ask_plan_question`, `standalone_plugin`。

⚠️ **单机限制**：`ClientToolManager` 用 module-level singleton + asyncio.Future — **非多 Pod 安全**。lea-platform 用 LangGraph `interrupt()` 天然解决。

---

## Section 7 — Agent / Skill 管理（用户域）

### Agents `/api/user/agents` (`api/user/agents.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `?scope=all\|system\|mine&category=&search=` | 列表 |
| GET | `/categories` `/super-agent-categories` | 分类 |
| GET/PUT/DELETE | `/default` | 默认 agent |
| GET | `/{id}` | 详情 |
| POST | `` | 创建 |
| PUT | `/{id}` | 更新 |
| DELETE | `/{id}` | 删除 |
| POST | `/generate` | AI 辅助配置 `{messages:[{role,content}]}` → `{reply, agent_config?, is_final}` |

```ts
AgentPublicResponse = {
  id, name, description?, avatar?, category?, category_label?,
  welcome_message?, soul_prompt?, allowed_skills?:string[],
  example_questions?:string[], is_system, is_mine, sort_order, is_enabled
}
```

用户域 Agent 无版本管理 / 草稿 / 发布状态，只有 `is_enabled` 开关。

### Skills（用户可见）
- **GET `/api/user/super-agent/skills?agent_id=`** — `SkillInfo[] {name, display_name, description, category, version}`；若指定 agent_id 且 agent 有 allowed_skills 则过滤。

Admin 层 skill CRUD 在 `/api/admin/super-skills/*`（见 Section 8）。

### 用户 KB
`/api/user/knowledge-bases` POST/GET/PATCH/DELETE

---

## Section 8 — Admin 面板 API 总览（~214 routes / 24 files）

| Router | 数量 | 用途 |
|---|---|---|
| `admin/users.py` | 10 | 用户 CRUD + 密码重置 + 批量导入 |
| `admin/roles.py` | 9 | RBAC |
| `admin/dashboard.py` | 4 | KPI |
| `admin/statistics.py` | 5 | 统计 |
| `admin/settings.py` | 26 | 全局开关 |
| `admin/config.py` | 23 | debate/models/roles/provider-health/channels/endpoints/models/fallback |
| `admin/agents.py` | 8 | 遗留 agent |
| `admin/super_agents.py` | 6 | Super-agent CRUD |
| `admin/super_skills.py` | 21 | Skill 全生命周期 + AI 辅助 + 回滚 + 热重载 |
| `admin/skill_logs.py` | 10 | SkillExecutionLog |
| `admin/tools.py` | 14 | Tool CRUD + tool sources (MCP/OpenAPI/Plugin) |
| `admin/plugins.py` | 8 | Coze/插件 |
| `admin/knowledge_bases.py` + `documents.py` | 9 | 公共 KB 管理 |
| `admin/experts.py` | 6 | 辩论专家 |
| `admin/debate_config.py` | 8 | debate model_distribution |
| `admin/token_usage.py` | 5 | 成本分析 |
| `admin/cron_jobs.py` | 7 | 定时任务 |
| `admin/subscription_monitor.py` | 8 | 订阅监控 |
| `admin/subscription_templates.py` | 8 | 模板 CRUD |
| `admin/tts.py` | 8 | TTS |
| `admin/memory_admin.py` | 4 | 向量记忆 |
| `admin/batch_import.py` | 4 | 批量 |
| `admin/intent_admin.py` | 3 | 意图配置 |

**Ph1 不做 admin**。Ph2 起分批引入，优先 users/roles/agents/tools/knowledge/statistics。

---

## Section 9 — Debate 模块

Router: `/api/user/debate`。**直接 SSE POST 响应**（非 async-202）。

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/config` | — | `{default_rounds, max_rounds, expert_count, timeout_seconds}` |
| POST | `/start` | `{topic, expert_count?, max_rounds?, discussion_mode?}` | `text/event-stream` |
| POST | `/{sid}/continue` | — | `text/event-stream` |
| POST | `/{sid}/ask` | `{expert_id, question}` | `text/event-stream` |
| POST | `/{sid}/end` | — | `text/event-stream` |
| GET | `/{sid}` | — | Debate detail |
| GET | `/experts/list` | — | `ExpertInfo[]` |

前端用 `fetch + ReadableStream`（POST 不能用 EventSource）。事件通过 `_convert_debate_event`（`debate.py:380`）归一化为标准 `{event, data}` 格式。

**⚠️ 单机限制**：`DebateOrchestrator._sessions` 是内存 dict。lea-platform 用 checkpointer 解决。

---

## Section 10 — 后端逻辑映射（LangGraph 移植策略）

| # | 能力 | 原实现位置 | 原框架 | lea-platform 移植策略 |
|---|---|---|---|---|
| 1 | **主循环** | `orchestrator.py:SuperAgentOrchestrator.chat_stream (l.605)` → dispatch to `_handle_skill`/`_handle_qa`/`_handle_debate`；think→tool→observe 在 `executors/forked_subagent.py:ForkedSubAgent.execute_stream (l.832)` | 裸 asyncio + 手写 OpenAI streaming (`openai_client_v2`)，无 LangChain。State machine + Strategy pattern (StaleSearch/DoneTool/CircuitBreaker) | LangGraph 子图：**agent node → tool node → observation node** 循环；保留熔断器（consecutive=3/8, total=15/30）作为 `should_continue` guard |
| 2 | **会话状态** | `context_store.py:ContextStore` + `session_manager.py:SessionManager` + `SuperAgentSession`（JSON 列 messages/context/message_nodes/current_leaf/revision） | 乐观锁 + 树结构节点池（regen/edit/switch-version） | 用 LangGraph `Checkpointer`（Postgres）。**保留树节点池**（版本 / 分支）作为 state field |
| 3 | **Tool / MCP** | `subagent_tool_executor.py:ToolExecutorMixin` + `tools/impl/*` + `tool_source_loader.py` | dict→callable resolution；`mcp` lib；OpenAPI stubs | 换 `langchain_core.tools.BaseTool` + `langchain-mcp-adapters` / `MultiServerMCPClient`。Client-tool bridge 作为 LangGraph HIL interrupt 节点 |
| 4 | **RAG** | `tools/impl/knowledge.py:kb_query` + `local_kb/service.py` + `pageindex/retrieval_router.py`（chunk vs tree）；公共 KB 走 `integrations/knowledge_base.py` | Chroma/Qdrant；PageIndex 独立引擎 | LangChain `Retriever` 包装。**PageIndexService 保留不动**（框架无关，质量高）|
| 5 | **Plan mode** | `plan_mode.py:PlanModeManager` + `plan_executor.py`；`submit_for_approval` 返 `asyncio.Future`，REST `/plans/{id}/{approve,reject}` 解析 | asyncio.Future 单机 | LangGraph `interrupt()` HIL；plan JSON 存 checkpointer；approve 调 `graph.update_state()` + resume。**丢弃 Future 机制** |
| 6 | **Sub-agent** | `executors/forked_subagent.py`；子 skill 通过特殊工具 `invoke_skill` 调 | 递归同进程 `execute_stream`，事件前缀 `sub_agent_*` | 每个 Skill 建独立 compiled subgraph；用 LangGraph `Send` 或 `subgraphs=True` streaming |
| 7 | **Parallel** | `parallel_orchestrator.py:ParallelOrchestrator (l.166)` + `task_decomposer.py` | asyncio.gather + LLM 综合 | LangGraph `Send` API 正好对上 —— 一节点映射为 N Sends，聚合节点收集 |
| 8 | **Peer review / verification** | `peer_reviewer.py:PeerReviewer.review (l.71)` + `output_verifier.py` + `diagnostic_agent.py`；`skill_handler.py:_verify_output (l.773)` 调 | LLM-as-judge + rule 预检 | 在 content 生成节点后加 verification 节点；复用 rubric |
| 9 | **Debate** | `debate_orchestrator.py:DebateOrchestrator (l.132)` — `_run_round/_coordinator_opening/_expert_speak/_final_summary`；session 内存 dict | 自研 asyncio + Expert dataclass | LangGraph 循环：expert-selector → expert-node → summarizer；state 存 checkpointer（**解多 Pod**）|
| 10 | **长期记忆** | `viking_memory/service.py:VikingMemoryService` — Qdrant/ByteDance Viking；`retrieve_memories()` step 1；`extract_and_store()` after chat；per-user asyncio.Lock | Qdrant + LLM 抽取/去重 | 用 `langgraph.store`（或现有 Qdrant collection）。**抽取/去重类保留**（provider-neutral）|
| 11 | **附件 / PageIndex** | Upload: `super_agent.py:upload_attachment (l.157)` → `attachment_service.py`；chat 中 `orchestration/attachment_processor.py`；PageIndex bg `pageindex/service.py:PageIndexService.request_index (l.183)` | asyncio bg + Postgres/Redis + claim lock | **PageIndexService 逐字保留**（provider-neutral, 设计好）。附件作为 `HumanMessage` content 附加 |
| 12 | **Token 计量** | `token_tracker.py:track_token_usage` → `token_usage_log` 表 via `asyncio.to_thread`；每处 `llm_usage` SSE 发射位置调 | 裸 psycopg | LangGraph callback / `on_llm_end` handler 插同一批行。极简 |
| 13 | **Auth** | Gen: `core/auth.py:create_access_token (l.48)` python-jose；Validate: `get_current_user (l.84)`, `get_user_from_token_str (l.113)`；bcrypt (72-byte cap) | python-jose + bcrypt | **整块搬**（无 LangChain 依赖）|

---

## Section 11 — 反模式 & 不搬清单

### ❌ 反模式 / 死代码
- **`orchestrator.py` 1773 行**god-object，含遗留 sync `_handle_qa`（`QA_MAX_TOOL_ITERATIONS=3`, hand-rolled `QA_TOOLS`, l.201-242）—— 只搬 ForkedSubAgent 路径
- **重复文件**：`debate_handler.py` vs `debate/handler.py`；`context_qa_handler.py` vs `orchestration/context_qa_handler.py`；`subagent_tool_executor.py` vs `utils/subagent_tool_executor.py`；`artifact_handler.py` vs `content/artifact_handler.py`。**只搬新版本**（`debate/`, `orchestration/`, `content/`, `utils/`）
- **`forked_subagent.py`** 是 stub，转发到 `executors/forked_subagent.py`。跳过 stub
- **`ClientToolManager` / `PlanModeManager`** in-process Future 单例，非多 Pod 安全 → LangGraph `interrupt()` 替换
- **`DebateOrchestrator._sessions`** 内存 dict → checkpointer 替换
- **两个 SSE broker**：`message_bus.py`（内部）+ `core/sse_broker.py`（生产）—— 只留后者
- **两个 attachment_service**：只用 `services/super_agent/attachment_service.py`（top-level）
- `/files/legacy/{filename}`（2026-04-25 已弃）跳过
- `latex_tools.py / duckdb_manager.py / duckdb_tools.py / docx_export/ / pdf_converter.py` — 只在对应 Skill 移植时带
- **前端 `api/chat.ts` + 后端 `api/user/chat.py`** 是旧单 agent 流，SuperAgent 视图不用，**Ph1 跳过**

### ⚠️ 前后端不对齐（需注意）
- 前端 `UserInfo.role: string` vs 后端 `roles: string[]`
- 前端 `SessionInfo.message_count / last_intent` 后端不返（前端渲染 undefined）
- 前端 `SSEEventType` union 只列 65 个，运行时实际 85+（**信运行时列表 `SuperAgent.vue:1357-1402`**，不信静态类型）
- Debate `discussion_mode` 后端接 free string，移植时收敛为 union

### 🚧 实验分支 / 破损
- `debate.py:_sync_debate_to_super_agent_session (l.462)` `flag_modified` 并发写脆弱 —— 用 ContextStore 节点池替代
- `sandbox_provisioner/` K8s 分支 TODO —— 只移植 Local/Docker
- `classroom/` 大表面，仅 Skill emit `classroom_*` 时用 —— MVP 可选
- `generators/` / `expert_generator.py` / `/generate` AI 辅助端点 —— nice-to-have，非 chat critical
