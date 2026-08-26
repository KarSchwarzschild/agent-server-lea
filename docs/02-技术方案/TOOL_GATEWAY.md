# Tool Gateway 设计文档

> LEA 平台 — 云端工具执行网关

## 1. 定位

Tool Gateway 是平台所有**云端工具调用**的统一入口，职责：

- **工具注册** — 管理工具定义、schema、执行类型
- **权限控制** — 租户/角色/Agent 级别的工具访问控制
- **凭证管理** — 外部 API Key 加密存储、按租户隔离、配额追踪
- **执行引擎** — 接收 tool_call，路由到正确后端（沙箱/外部API/内部服务/MCP）
- **MCP 代理** — 接入外部 MCP Server，统一加权限和 Key

**不管的事：**
- 本地工具的执行和权限（客户端自行控制）
- 沙箱生命周期（独立 E2B 服务管理）
- Agent 编排逻辑（Go 引擎的事）

## 2. 架构

```
┌──────────────┐
│  Client SDK   │── 本地工具：客户端直接执行
└──────┬────────┘
       │ cloud tool_call
       ▼
┌──────────────────┐
│  Go Agent Engine  │── Agent Loop / LLM 编排
└──────┬────────────┘
       │ POST /tools/execute
       ▼
┌─────────────────────────────────────────────┐
│          Tool Gateway (Python/FastAPI)        │
│                                               │
│  ┌───────────┐ ┌────────────┐ ┌───────────┐ │
│  │ Permission │ │ Credential │ │  Registry  │ │
│  └───────────┘ └────────────┘ └───────────┘ │
│  ┌─────────────────────────────────────────┐ │
│  │            Execution Router              │ │
│  │  sandbox → E2B API                      │ │
│  │  api     → HTTP Adapter (通用模板)       │ │
│  │  internal→ 内部服务 RPC                  │ │
│  │  mcp     → MCP Client Proxy             │ │
│  └─────────────────────────────────────────┘ │
└──────┬──────────┬──────────┬──────────┬──────┘
       │          │          │          │
       ▼          ▼          ▼          ▼
   E2B 沙箱   外部 API    lea-rag    MCP Servers
  (独立部署)  (搜索/邮件) (KB/Memory) (第三方)
```

## 3. 工具分类

| 执行类型 | 说明 | 示例 |
|----------|------|------|
| `local` | 客户端本地执行，不经 Gateway | read_file, write_file, terminal |
| `sandbox` | 转发到 E2B 沙箱 | code_exec, cloud_read_file |
| `api` | 通用 HTTP Adapter 调外部 | web_search, send_email |
| `internal` | 调平台内部微服务 | kb_search, memory_search |
| `mcp` | 代理外部 MCP Server | 用户配置的第三方 MCP |

### 本地 vs 云端判定原则

| 放本地 | 放云端 |
|--------|--------|
| 操作本地资源（文件/剪贴板/IDE） | 需要敏感 Key |
| 用户隐私数据不出机器 | 需要计量计费 |
| 对延迟极敏感 | 需要审计/过滤 |
| | 本身需要联网（搜索/邮件） |

## 4. 核心模块

### 4.1 Tool Registry（工具注册中心）

管理所有工具的元信息。客户端 session init 时拉取可用工具列表。

### 4.2 Permission（权限控制）

三级粒度：
- **租户级** — 整个租户开关某工具
- **角色级** — admin / user / guest 不同权限
- **Agent 级** — 特定 Agent 只能用指定工具子集

校验时机：`/tools/execute` 入口处，命中缓存 < 1ms。

### 4.3 Credential Vault（凭证保管）

- 平台公共 Key（默认，有配额上限）
- 租户自带 Key（BYOK，优先使用）
- 加密存储（AES-256），运行时解密注入
- 配额追踪：per-tenant per-tool 用量统计

### 4.4 Execution Router（执行路由）

根据 `execution_type` 分发：

```python
match tool.execution_type:
    case "sandbox":  return await sandbox_executor.run(call, credential)
    case "api":      return await http_adapter.run(call, credential)
    case "internal": return await internal_caller.run(call, credential)
    case "mcp":      return await mcp_proxy.run(call, credential)
```

### 4.5 MCP Proxy（MCP 代理）

- 连接用户配置的外部 MCP Server
- 维护 MCP session（SSE/stdio transport）
- 加上权限检查 + credential 注入
- 将 MCP tool list 同步到 Registry

### 4.6 HTTP Adapter（通用 HTTP 适配器）

用户注册自定义 API 工具时填模板，Gateway 渲染执行：

```yaml
name: "my_crm_search"
endpoint: "https://api.mycrm.com/search"
method: POST
headers_template:
  Authorization: "Bearer {{credential.api_key}}"
request_mapping:
  body: { "q": "{{params.query}}", "limit": "{{params.top_k}}" }
response_mapping:
  result: "$.data.items"
schema:
  params: { query: string, top_k: int }
  returns: { items: array }
```

## 5. 数据模型

### tools — 工具定义

```sql
CREATE TABLE tools (
    id            BIGINT PRIMARY KEY AUTO_INCREMENT,
    name          VARCHAR(100) NOT NULL UNIQUE,     -- 调用名 (web_search)
    display_name  VARCHAR(200),                     -- 展示名 (联网搜索)
    description   TEXT,
    category      VARCHAR(50),                      -- search / code / knowledge / custom
    execution_type ENUM('local','sandbox','api','internal','mcp') NOT NULL,
    schema_json   JSON NOT NULL,                    -- 参数 + 返回值 JSON Schema
    endpoint      VARCHAR(500),                     -- api/internal 的 URL
    sandbox_config JSON,                            -- 镜像/超时/资源限制
    http_adapter_config JSON,                       -- 通用 HTTP 模板配置
    mcp_server_id BIGINT,                           -- 关联的 MCP Server
    supports_local  BOOLEAN DEFAULT FALSE,
    supports_cloud  BOOLEAN DEFAULT TRUE,
    enabled       BOOLEAN DEFAULT TRUE,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
```

### tool_permissions — 权限规则

```sql
CREATE TABLE tool_permissions (
    id          BIGINT PRIMARY KEY AUTO_INCREMENT,
    tenant_id   VARCHAR(100) NOT NULL,
    role        VARCHAR(50) DEFAULT '*',            -- * = all roles
    agent_id    BIGINT NULL,                        -- NULL = 不限 agent
    tool_id     BIGINT NOT NULL,
    allowed     BOOLEAN DEFAULT TRUE,
    rate_limit  INT DEFAULT 0,                      -- 0 = 不限
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tool_id) REFERENCES tools(id)
);
```

### tool_credentials — 凭证管理

```sql
CREATE TABLE tool_credentials (
    id          BIGINT PRIMARY KEY AUTO_INCREMENT,
    tenant_id   VARCHAR(100) NOT NULL,
    tool_id     BIGINT NOT NULL,
    provider    VARCHAR(100),                       -- google / bing / custom
    api_key_enc VARCHAR(500) NOT NULL,              -- AES-256 加密
    api_base    VARCHAR(500),
    extra_config JSON,
    quota_limit INT DEFAULT 0,                      -- 0 = 不限
    quota_used  INT DEFAULT 0,
    expires_at  DATETIME NULL,
    is_platform_default BOOLEAN DEFAULT FALSE,      -- 平台公共 key
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tool_id) REFERENCES tools(id)
);
```

### mcp_servers — MCP 接入配置

```sql
CREATE TABLE mcp_servers (
    id          BIGINT PRIMARY KEY AUTO_INCREMENT,
    tenant_id   VARCHAR(100) NOT NULL,
    name        VARCHAR(200) NOT NULL,
    transport   ENUM('sse','stdio','streamable_http') DEFAULT 'sse',
    endpoint    VARCHAR(500),                       -- SSE/HTTP URL
    command     VARCHAR(500),                       -- stdio 启动命令
    env_config  JSON,                              -- 环境变量
    auth_config JSON,                              -- 认证配置
    tool_filter JSON,                              -- 只暴露部分 tools
    enabled     BOOLEAN DEFAULT TRUE,
    last_sync_at DATETIME,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

## 6. API 设计

### 6.1 Session 能力获取

```
POST /session/capabilities
Body: { "tenant_id": "t1", "user_id": "u1", "role": "user", "agent_id": 5 }
Response: {
  "tools": [
    { "name": "web_search", "schema": {...}, "execution_type": "api" },
    { "name": "kb_search", "schema": {...}, "execution_type": "internal" },
    ...
  ],
  "credentials": {
    "web_search": { "api_key": "sk-xxx", "expires_in": 300 }
  },
  "ttl": 300
}
```

客户端/Go 引擎在 session 开始时调一次，缓存 5 分钟，过期 refresh。

### 6.2 工具执行

```
POST /tools/execute
Headers: X-Tenant-ID, X-User-ID, X-Agent-ID
Body: {
  "tool_name": "web_search",
  "params": { "query": "latest news about AI", "top_k": 5 },
  "session_id": "sess_abc123"
}
Response: {
  "status": "success",
  "result": { ... },
  "usage": { "credits": 1 }
}
```

对于 streaming 场景（sandbox 代码执行）：

```
POST /tools/execute?stream=true
Response: SSE stream
  event: output
  data: {"type": "stdout", "content": "Hello world\n"}

  event: artifact
  data: {"type": "file", "path": "output.png", "url": "..."}

  event: done
  data: {"status": "success", "usage": {"credits": 2}}
```

### 6.3 工具注册 (Admin)

```
POST   /admin/tools            — 创建工具
GET    /admin/tools            — 列表
PUT    /admin/tools/{id}       — 更新
DELETE /admin/tools/{id}       — 删除
```

### 6.4 权限管理 (Admin)

```
POST   /admin/permissions      — 创建规则
GET    /admin/permissions?tenant_id=x  — 查询
PUT    /admin/permissions/{id} — 更新
DELETE /admin/permissions/{id} — 删除
```

### 6.5 凭证管理 (Admin)

```
POST   /admin/credentials      — 添加 Key
GET    /admin/credentials?tenant_id=x  — 列表（key 脱敏）
PUT    /admin/credentials/{id} — 更新
DELETE /admin/credentials/{id} — 删除
```

### 6.6 MCP Server 管理

```
POST   /admin/mcp-servers           — 注册 MCP Server
GET    /admin/mcp-servers           — 列表
POST   /admin/mcp-servers/{id}/sync — 同步工具列表
DELETE /admin/mcp-servers/{id}      — 删除
```

## 7. Session Capability 机制

### 流程

```
1. Session 建立 → Go Engine 调 POST /session/capabilities
2. Gateway 查 permissions + credentials → 返回 capability bundle
3. Go Engine / Client 缓存 bundle（TTL 5min）
4. 每次 tool_call → 本地查 bundle.tools 确认有权限 → 执行
5. TTL 到期 → 重新 POST /session/capabilities refresh
```

### 设计要点

- **bundle 包含 credential** 时仅限客户端本地执行的 API 类工具（如客户端侧搜索）
- 云端执行的工具不下发 key（Gateway 自己注入）
- bundle 中 key 用短期有效 token 包装，过期自动失效
- 权限变更（admin 操作）不实时推送，最多延迟一个 TTL 窗口

## 8. 执行引擎详细

### 8.1 Sandbox 执行器

```python
class SandboxExecutor:
    async def run(self, call: ToolCall, credential: dict) -> ToolResult:
        # 1. 获取/创建 sandbox session（E2B 协议）
        sandbox = await self.e2b_client.get_or_create(
            session_id=call.session_id,
            template=tool.sandbox_config.get("template", "default")
        )
        # 2. 执行
        result = await sandbox.run_code(call.params["code"])
        # 3. 返回（支持 streaming）
        return ToolResult(output=result.stdout, artifacts=result.files)
```

### 8.2 HTTP Adapter 执行器

```python
class HTTPAdapterExecutor:
    async def run(self, call: ToolCall, credential: dict) -> ToolResult:
        config = tool.http_adapter_config
        # 1. 渲染模板
        headers = render_template(config["headers_template"], credential)
        body = render_template(config["request_mapping"]["body"], call.params)
        # 2. 发请求
        resp = await httpx.post(config["endpoint"], headers=headers, json=body)
        # 3. 提取结果
        result = jsonpath_extract(resp.json(), config["response_mapping"]["result"])
        return ToolResult(output=result)
```

### 8.3 Internal 执行器

```python
class InternalExecutor:
    # 内部服务路由表
    ROUTES = {
        "kb_search": "http://lea-rag:8300/kb/search",
        "memory_search": "http://lea-rag:8300/memory/search",
    }
    async def run(self, call: ToolCall, credential: dict) -> ToolResult:
        url = self.ROUTES[call.tool_name]
        resp = await httpx.post(url, json=call.params)
        return ToolResult(output=resp.json())
```

### 8.4 MCP Proxy 执行器

```python
class MCPProxyExecutor:
    async def run(self, call: ToolCall, credential: dict) -> ToolResult:
        server = await self.get_mcp_session(call.mcp_server_id)
        result = await server.call_tool(call.tool_name, call.params)
        return ToolResult(output=result.content)
```

## 9. 部署

```yaml
# docker-compose 片段
lea-tools:
  build: ./packages/lea-tools
  ports:
    - "8400:8400"
  environment:
    - DATABASE_URL=mysql+pymysql://...
    - ENCRYPTION_KEY=...          # 凭证加密密钥
    - E2B_API_URL=http://e2b:3000
    - LEA_RAG_URL=http://lea-rag:8300
  depends_on:
    - mysql
    - redis
```

独立部署，端口 8400。与其他服务关系：

| 服务 | 端口 | Tool Gateway 的关系 |
|------|------|---------------------|
| Go Agent Engine | 8100 | 调用方 |
| lea-rag | 8300 | 被调用（internal 工具） |
| Tool Gateway | 8400 | 本服务 |
| E2B Sandbox | 3000 | 被调用（sandbox 工具） |

## 10. 包结构（初步）

```
packages/lea-tools/
├── pyproject.toml
└── src/lea_tools/
    ├── server.py              # FastAPI app + 路由注册
    ├── config.py              # 配置（DB/Redis/加密/E2B/内部服务URL）
    ├── db/
    │   ├── database.py
    │   └── models.py          # tools / permissions / credentials / mcp_servers
    ├── api/
    │   ├── session.py         # /session/capabilities
    │   ├── execute.py         # /tools/execute
    │   ├── admin_tools.py     # Admin CRUD
    │   ├── admin_perms.py
    │   ├── admin_creds.py
    │   └── admin_mcp.py
    ├── executor/
    │   ├── router.py          # 按 execution_type 分发
    │   ├── sandbox.py         # E2B 调用
    │   ├── http_adapter.py    # 通用 HTTP 模板
    │   ├── internal.py        # 内部服务调用
    │   └── mcp_proxy.py       # MCP Client
    ├── services/
    │   ├── permission.py      # 权限校验逻辑
    │   ├── credential.py      # Key 加解密 + 配额
    │   └── registry.py        # 工具注册同步
    └── utils/
        ├── crypto.py          # AES 加解密
        └── template.py        # 模板渲染引擎
```

## 11. MVP 范围

Phase 1（先跑通链路）：
- [ ] 工具 CRUD + Registry
- [ ] Permission 基础校验（tenant + role 级）
- [ ] Credential 存储 + 注入
- [ ] Internal executor（调 lea-rag）
- [ ] HTTP Adapter executor（接 web_search）
- [ ] /session/capabilities + /tools/execute

Phase 2（扩展能力）：
- [ ] Sandbox executor（对接已有 E2B）
- [ ] MCP Proxy（接入外部 MCP）
- [ ] Agent 级权限
- [ ] 配额追踪 + 用量统计
- [ ] 通用 HTTP Adapter 模板管理 UI

Phase 3（平台化）：
- [ ] 用户自建 MCP 接入
- [ ] Key 轮转 + 告警
- [ ] 工具市场（共享/订阅）

## 12. KB / Memory 调用策略

知识库检索和记忆召回是特殊的 internal 工具 — 是否调用、何时调用由 **per-agent 配置**决定。

### 配置模型

Agent 表新增字段：

```sql
ALTER TABLE agents ADD COLUMN kb_mode ENUM('always','tool','off') DEFAULT 'tool';
ALTER TABLE agents ADD COLUMN memory_mode ENUM('always','tool','off') DEFAULT 'always';
```

| 模式 | 行为 | 适用场景 |
|------|------|----------|
| `always` | Go 引擎在调 LLM 之前自动查，结果注入 system context | 客服 bot、FAQ、文档问答 |
| `tool` | 作为工具暴露给 LLM，模型自己决定是否调用 | 通用助手、编程助手 |
| `off` | 不使用 | 纯创作类 agent、闲聊 |

### 推荐默认值

- **memory_mode = `always`** — 记忆量小、召回快（< 50ms），用户个性化上下文几乎总是有用
- **kb_mode = `tool`** — 知识库查询较重（100-500ms），多数轮次不需要，让模型判断更省资源

### 执行流对比

```
kb_mode = "always"（前置检索）:
  user message
    → Go Engine 自动调 lea-rag /kb/search (query=user_message)
    → 结果注入 system message: "以下是相关参考资料: ..."
    → LLM 生成回答（带引用）

kb_mode = "tool"（工具模式）:
  user message
    → LLM 看到可用工具列表中有 kb_search
    → LLM 判断需要搜索 → 构造优化后的 query → tool_call("kb_search", {...})
    → Go Engine → Tool Gateway → lea-rag → 结果返回
    → LLM 根据结果生成回答
```

### tool 模式的优势

- 模型可以**改写 query** — "刚才那个方案的第三点" → 组合上下文生成精准检索词
- 可以**多轮检索** — 第一次结果不够，换个角度再搜
- 可以**跳过无关轮次** — "你好""谢谢"不浪费检索资源

### always 模式的优势

- **零遗漏** — 不依赖模型判断，保证每轮都有上下文
- **少一轮 LLM 调用** — 不需要模型先决定"要不要搜"，直接给结果
- **延迟更低** — 前置检索可以和其他预处理并行

### 实现位置

这个逻辑在 **Go Agent Engine** 侧实现（不在 Tool Gateway）：

```go
// Go Engine pseudo-code
func handleMessage(msg Message, agent Agent) {
    // 1. always 模式：前置检索
    if agent.KBMode == "always" {
        kbResults := callToolGateway("kb_search", msg.Content)
        injectContext(kbResults)
    }
    if agent.MemoryMode == "always" {
        memResults := callToolGateway("memory_search", msg.Content)
        injectContext(memResults)
    }

    // 2. 构造工具列表
    tools := getAvailableTools(agent)
    if agent.KBMode == "tool" {
        tools = append(tools, kbSearchTool)
    }
    if agent.MemoryMode == "tool" {
        tools = append(tools, memorySearchTool)
    }

    // 3. 调 LLM
    response := callLLM(msg, tools, context)
}
```

Tool Gateway 不需要感知这个策略 — 它只管执行传入的 tool_call，无论是 Go Engine 前置调的还是 LLM tool_call 触发的。
