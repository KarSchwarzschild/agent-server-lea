# Go Agent Engine 对接规范

> LEA Platform — Go 引擎 ↔ Python 服务接口合同
> 
> 版本: v1.0 | 日期: 2025-07-17

---

## 1. 架构概览

```
Go Agent Engine (port 8100)
    │
    ├── POST lea-server:8000/api/agents/{id}     ← 读 Agent 完整配置
    │
    ├── POST lea-tools:8400/session/capabilities  ← 获取可用工具列表
    ├── POST lea-tools:8400/tools/execute         ← 执行云端工具
    │
    ├── POST lea-rag:8300/kb/search              ← 知识库检索
    ├── POST lea-rag:8300/memory/search          ← 记忆检索
    └── POST lea-rag:8300/memory/add             ← 记忆写入
```

---

## 2. Agent 配置获取

### `GET /api/agents/{agent_id}`

Go 引擎在 session 建立时读取 agent 完整配置。

**Response:**
```json
{
  "id": 1,
  "name": "customer-service",
  "soul_prompt": "你是一个专业的客服助手...",
  "model": "qwen-max-latest",
  "temperature": 7,
  "max_turns": 20,
  "max_tokens": 4096,
  "kb_mode": "always",
  "memory_mode": "always",
  "timeout_seconds": 120,
  "example_questions": ["如何退款？", "物流多久到？"],
  "enabled": true
}
```

**关键字段说明:**

| 字段 | 类型 | 说明 |
|------|------|------|
| soul_prompt | string | 系统提示词（注入 system message） |
| model | string | LLM 模型名，空字符串=使用租户默认 |
| temperature | int | x10 存储（7 = 0.7） |
| max_turns | int | agent loop 最大轮次 |
| kb_mode | enum | `always`=每轮前置检索, `tool`=LLM 决定, `off`=禁用 |
| memory_mode | enum | 同上 |
| timeout_seconds | int | 整个 agent 执行超时 |

---

## 3. Tool Gateway 接口

### 3.1 `POST /session/capabilities`

Session 建立时调一次，获取该 agent 可用的工具列表。结果缓存 5 分钟。

**Request:**
```json
{
  "tenant_id": "t1",
  "user_id": "u123",
  "role": "user",
  "agent_id": 5
}
```

**Response:**
```json
{
  "tools": [
    {
      "name": "kb_search",
      "display_name": "知识库检索",
      "description": "在绑定的知识库中搜索相关内容",
      "schema": { "type": "object", "properties": { "query": { "type": "string" } } },
      "execution_type": "internal"
    },
    {
      "name": "web_search",
      "display_name": "联网搜索",
      "schema": { "type": "object", "properties": { "query": { "type": "string" } } },
      "execution_type": "api"
    }
  ],
  "ttl": 300,
  "cached": false
}
```

**引擎使用方式：**
- 将 `tools[].schema` 转为 LLM function calling 的 tools 参数
- `kb_mode=tool` 时才把 `kb_search` 放入工具列表
- `kb_mode=always` 时 kb_search 不放入列表（引擎自行前置调用）

### 3.2 `POST /tools/execute`

LLM 返回 tool_call 时，引擎调此接口执行。

**Request Headers:**
```
X-Tenant-ID: t1
X-User-Role: user
X-Agent-ID: 5          (可选)
```

**Request Body:**
```json
{
  "tool_name": "web_search",
  "params": { "query": "latest AI news" },
  "session_id": "sess_abc123"
}
```

**Response (成功):**
```json
{
  "status": "success",
  "result": { "items": [...] },
  "error": null,
  "usage": { "credits": 1 }
}
```

**Response (失败):**
```json
{
  "status": "error",
  "result": null,
  "error": "rate limit exceeded",
  "usage": {}
}
```

**HTTP 状态码:**
| Code | 含义 |
|------|------|
| 200 | 执行完成（看 status 区分成功/失败） |
| 403 | 权限拒绝 |
| 404 | 工具不存在 |
| 429 | 超限 |

---

## 4. lea-rag 检索接口

### 4.1 `POST /kb/search`

**Request:**
```json
{
  "query": "如何申请退款",
  "kb_ids": [1, 3],
  "top_k": 5,
  "strategy": "hybrid"
}
```

**Response:**
```json
{
  "results": [
    {
      "content": "退款流程：1. 进入订单页...",
      "score": 0.92,
      "metadata": {
        "document_id": 42,
        "kb_id": 1,
        "filename": "退款指南.pdf",
        "chunk_position": 3
      }
    }
  ]
}
```

### 4.2 `POST /memory/search`

**Request:**
```json
{
  "query": "用户偏好",
  "user_id": "u123",
  "top_k": 5
}
```

**Response:**
```json
{
  "memories": [
    { "content": "用户偏好暗色主题", "relevance": 0.85, "created_at": "2025-07-10T..." }
  ]
}
```

### 4.3 `POST /memory/add`

**Request:**
```json
{
  "user_id": "u123",
  "content": "用户说他是一名教师，教授计算机科学"
}
```

**Response:**
```json
{ "id": 42, "status": "ok" }
```

---

## 5. Go 引擎核心循环伪码

```go
func HandleMessage(session Session, userMsg string) {
    agent := GetAgentConfig(session.AgentID)
    
    // 1. always 模式前置检索
    var context []string
    if agent.KBMode == "always" {
        kbResult := POST("lea-rag:8300/kb/search", {query: userMsg, kb_ids: session.KBIDs})
        context = append(context, formatKBResults(kbResult))
    }
    if agent.MemoryMode == "always" {
        memResult := POST("lea-rag:8300/memory/search", {query: userMsg, user_id: session.UserID})
        context = append(context, formatMemories(memResult))
    }

    // 2. 构造工具列表
    caps := POST("lea-tools:8400/session/capabilities", {
        tenant_id: session.TenantID, user_id: session.UserID,
        role: session.Role, agent_id: agent.ID,
    })
    tools := filterToolsByMode(caps.Tools, agent)

    // 3. Agent Loop
    for turn := 0; turn < agent.MaxTurns; turn++ {
        resp := CallLLM(agent.Model, agent.SoulPrompt, context, tools, messages)
        
        if resp.HasToolCalls() {
            for _, tc := range resp.ToolCalls {
                result := POST("lea-tools:8400/tools/execute", {
                    tool_name: tc.Name, params: tc.Args, session_id: session.ID,
                }, headers: {X-Tenant-ID, X-User-Role, X-Agent-ID})
                messages = append(messages, ToolResult{tc.ID, result})
            }
        } else {
            // 最终回复
            SendSSE(session, resp.Content)
            break
        }
    }

    // 4. 记忆提取（异步）
    go ExtractAndSaveMemory(session.UserID, messages)
}
```

---

## 6. 错误处理约定

| 场景 | 引擎行为 |
|------|----------|
| Tool Gateway 返回 403 | 告知用户"该工具暂不可用" |
| Tool Gateway 返回 429 | 等待后重试一次，仍失败则告知用户 |
| Tool execute status=error | 将 error 信息作为 tool result 返回给 LLM |
| lea-rag 返回空结果 | 正常：无匹配知识，LLM 基于自身知识回答 |
| 超时 (timeout_seconds) | 中止当前轮，返回已有部分回复 |

---

## 7. 认证

所有内部服务间调用走内网，暂不加认证。生产环境可选：
- Service Mesh (Istio mTLS)
- 内部 JWT (服务级，不是用户级)
- Header `X-Internal-Token` 简单校验
