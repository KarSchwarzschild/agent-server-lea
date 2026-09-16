# 第三方接入 API 文档

> 面向第三方平台调用。所有服务通过 HTTP REST 接口暴露。
> 认证方式由 gateway 层统一处理，传入 `X-User-ID`, `X-Tenant-ID`, `X-User-Role` 等身份信息后，内部服务信任这些头字段。

---

## 1. lea-agent (主对话引擎，`:8100`)

### 1.1 聊天接口

#### POST `/api/chat/stream`
发送消息并以 SSE 流式接收回复（主链路）。

**请求**
```json
{
  "session_id": "string",
  "message": "string",
  "agent_id": 0,
  "attachments": []  // 可选，会话级挂载的资源
}
```

**响应** — Server-Sent Events，事件类型有：
- `session` — 会话信息
- `start` — 开始生成
- `delta` — 文本增量（纯文本，非 JSON）
- `tool_event` — 工具调用事件（`toolName`, `toolUseID`, `type`, `status` 等）
- `result` — 最终结果（包含 token 使用量、迭代次数）
- `done` — 流结束

**权限** — 需要 `X-User-ID`, `X-Tenant-ID`

---

#### GET `/api/chat/stream/:session_id`
断线重连：从指定事件 ID 补齐，再接直播。

**查询参数**
- `from` — 最后收到的事件 ID（缺省从头开始）
- `tenant_id` — 租户 ID

**响应** — SSE 流

**权限** — 需要 `X-Tenant-ID` + `X-User-ID`（跨副本需 Redis 配置）

---

#### POST `/api/chat/send`
发送消息（立即返回）。结果通过另外的轮询或 webhook 获取（旧链路，不推荐）。

**请求**
```json
{
  "session_id": "string",
  "message": "string",
  "agent_id": 0
}
```

**响应**
```json
{
  "session_id": "string",
  "turn_id": "string"
}
```

---

#### GET `/api/chat/status/:session_id`
查询会话是否还在生成中。

**响应**
```json
{
  "running": true,
  "turn_id": "string"
}
```

---

#### POST `/api/chat/stop/:session_id`
中断当前生成。

**响应** `204 No Content`

---

#### POST `/api/chat/attachments/upload`
上传附件（图片、文档等）。

**请求** — multipart/form-data
- `file` — 文件二进制
- `session_id` — 会话 ID（可选）

**响应**
```json
{
  "attachment_id": "string",
  "filename": "string",
  "size": 1024,
  "mime_type": "image/png"
}
```

---

#### GET `/api/chat/attachments/:id/download`
下载附件原件。

**响应** — 文件流

---

### 1.2 会话管理

#### POST `/api/sessions`
创建新会话。

**请求**
```json
{
  "agent_id": 0,
  "project_id": null
}
```

**响应**
```json
{
  "id": "sess-123456",
  "agent_id": 0,
  "project_id": null,
  "created_at": "2026-09-14T10:00:00+08:00"
}
```

---

#### GET `/api/sessions`
列出所有会话。

**查询参数**
- `project_id` — 按项目过滤（可选）

**响应**
```json
[
  {
    "id": "sess-123456",
    "agent_id": 0,
    "project_id": null,
    "title": "第一次对话",
    "created_at": "2026-09-14T10:00:00+08:00",
    "updated_at": "2026-09-14T10:05:00+08:00"
  }
]
```

---

#### GET `/api/sessions/:id/messages`
获取会话历史消息。

**查询参数**
- `limit` — 最大条数（默认 50）
- `offset` — 分页偏移（默认 0）

**响应**
```json
{
  "messages": [
    {
      "id": "msg-123",
      "role": "user|assistant",
      "content": "string",
      "created_at": "2026-09-14T10:00:00+08:00"
    }
  ],
  "total": 100
}
```

---

#### DELETE `/api/sessions/:id`
归档会话（软删除）。

**响应** `204 No Content`

---

### 1.3 会话级挂载

#### GET `/api/sessions/:id/attachments`
列出该会话当前的临时挂载（工具、知识库、技能等）。

**响应**
```json
[
  {
    "kind": "tool|skill|kb|mcp_server",
    "ref_id": "123",
    "ref_name": "Weather Tool"
  }
]
```

---

#### POST `/api/sessions/:id/attachments`
给会话临时挂载资源（不改 agent 配置，仅限本会话）。

**请求**
```json
{
  "kind": "tool",
  "ref_id": "123"
}
```

**响应**
```json
{
  "applies": "next_message"
}
```

---

#### DELETE `/api/sessions/:id/attachments/:kind/:ref_id`
移除会话级挂载。

**响应** `204 No Content`

---

### 1.4 激活 Agent（专家切换）

#### PUT `/api/sessions/:id/active-agent`
切换该会话使用的 agent（专家/专家团）。

**请求**
```json
{
  "agent_id": 0
}
```

**响应**
```json
{
  "applies": "next_message"
}
```

> `agent_id=0` 表示回到默认 super-agent。

---

### 1.5 文件操作

#### GET `/api/files/list`
列出会话产物（workspace 里的文件）。

**查询参数**
- `session_id` — 必填

**响应**
```json
{
  "files": [
    {
      "path": "output/report.txt",
      "name": "report.txt",
      "dir": "output",
      "type": "txt",
      "size": 1024,
      "mtime": 1694688000000,
      "in_output": true,
      "status": "ready"
    }
  ],
  "store": "nfs"
}
```

---

#### GET `/api/files/download`
下载产物文件。

**查询参数**
- `session_id` — 必填
- `path` — 文件路径（必填）

**响应** — 文件流

---

#### POST `/api/sessions/:id/files/import`
从用户文件库导入文件到这个会话。

**请求**
```json
{
  "file_id": "string",
  "kind": "upload|artifact"
}
```

**响应** `204 No Content`

---

#### POST `/api/sessions/:id/files/publish`
将会话中生成的文件发布到用户长期文件库。

**请求**
```json
{
  "path": "output/report.txt",
  "name": "Report"
}
```

**响应**
```json
{
  "file_id": "string",
  "name": "Report"
}
```

---

### 1.6 项目管理

#### GET `/api/projects`
列出项目。

**响应**
```json
[
  {
    "id": 1,
    "name": "项目A",
    "description": "xxx",
    "session_count": 5,
    "is_archived": false
  }
]
```

---

#### POST `/api/projects`
创建项目。

**请求**
```json
{
  "name": "项目A",
  "description": "xxx"
}
```

**响应**
```json
{
  "id": 1,
  "name": "项目A"
}
```

---

#### PUT `/api/projects/:id`
更新项目。

**请求**
```json
{
  "name": "项目A",
  "description": "xxx"
}
```

---

#### POST `/api/projects/:id/archive`
归档项目。

**响应** `204 No Content`

---

#### DELETE `/api/projects/:id`
删除项目。

**响应** `204 No Content`

---

### 1.7 用户文件库

#### GET `/api/user/storage`
查看用户存储使用情况。

**响应**
```json
{
  "used_bytes": 1048576,
  "limit_bytes": 10737418240,
  "file_count": 10
}
```

---

#### GET `/api/user/files`
列出用户的长期文件库（上传件 + 交付成品）。

**查询参数**
- `kind` — 过滤：`upload|artifact|all`（默认 all）
- `limit` — 分页大小
- `offset` — 分页偏移

**响应**
```json
{
  "files": [
    {
      "id": "file-123",
      "name": "Report",
      "kind": "artifact",
      "size": 1024,
      "created_at": "2026-09-14T10:00:00+08:00"
    }
  ],
  "total": 100
}
```

---

#### DELETE `/api/user/files/:kind/:id`
删除用户文件。

**响应** `204 No Content`

---

---

## 2. lea-config (配置与编排，`:8500`)

> 内部服务，通常由 lea-agent 反代。但第三方也可直接调用。

### 2.1 Agent 管理

#### GET `/api/agents`
列出 agents。

**查询参数**
- `category` — 按类别过滤
- `visible_only` — 只显示对当前用户可见的（默认 true）

**响应**
```json
[
  {
    "id": 1,
    "name": "Weather Expert",
    "description": "xxx",
    "avatar": "url",
    "category": "expert",
    "model": "qwen3-max",
    "enabled": true
  }
]
```

---

#### GET `/api/agents/:agent_id`
获取单个 agent 完整配置。

**响应**
```json
{
  "id": 1,
  "name": "Weather Expert",
  "soul_prompt": "You are...",
  "skills": [{ "id": 1, "name": "Weather" }],
  "tools": [{ "id": 1, "name": "curl" }],
  "kbs": [{ "id": "kb-123", "name": "Weather KB" }],
  "mcp_servers": []
}
```

---

#### POST `/api/agents`
创建 agent。

**请求**
```json
{
  "name": "Expert Name",
  "description": "xxx",
  "soul_prompt": "You are...",
  "model": "qwen3-max",
  "category": "expert"
}
```

---

#### PUT `/api/agents/:agent_id`
更新 agent。

**请求** — 同创建

---

#### DELETE `/api/agents/:agent_id`
删除 agent。

---

#### GET `/api/agents/:agent_id/bindings`
获取 agent 的所有资源绑定（skills, tools, kbs, mcp）。

---

#### POST `/api/agents/:agent_id/skills`
绑定 skill 到 agent。

**请求**
```json
{
  "skill_id": 123
}
```

---

#### DELETE `/api/agents/:agent_id/skills/:skill_id`
解绑 skill。

---

#### POST `/api/agents/:agent_id/tools`
绑定工具到 agent。

**请求**
```json
{
  "tool_id": 123
}
```

---

#### DELETE `/api/agents/:agent_id/tools/:tool_id`
解绑工具。

---

#### POST `/api/agents/:agent_id/kbs`
绑定知识库到 agent。

**请求**
```json
{
  "kb_id": "kb-123"
}
```

---

#### DELETE `/api/agents/:agent_id/kbs/:kb_id`
解绑知识库。

---

#### POST `/api/agents/:agent_id/mcp`
绑定 MCP server 到 agent。

**请求**
```json
{
  "mcp_server_id": 123
}
```

---

#### DELETE `/api/agents/:agent_id/mcp/:mcp_server_id`
解绑 MCP server。

---

#### GET `/api/agents/:agent_id/share-check`
检查能否将 agent 分享（团队递归检查）。

---

#### POST `/api/agents/:agent_id/share`
分享 agent 到租户其他成员。

---

#### POST `/api/agents/:agent_id/unshare`
取消分享。

---

### 2.2 Skills（技能包）

#### GET `/api/skills`
列出 skills。

**响应**
```json
[
  {
    "id": 123,
    "name": "Weather Toolkit",
    "description": "xxx",
    "version": "1.0.0",
    "enabled": true
  }
]
```

---

#### GET `/api/skills/:skill_id`
获取 skill 详情。

---

#### POST `/api/skills`
上传 skill。

**请求** — multipart/form-data
- `bundle` — zip 文件
- `name` — skill 名称
- `description` — 描述

---

#### PUT `/api/skills/:skill_id`
更新 skill 元数据。

---

#### DELETE `/api/skills/:skill_id`
删除 skill。

---

#### POST `/api/skills/:skill_id/publish`
提交 skill 发布申请（上架审核）。

---

#### GET `/api/skills/publish-requests`
（管理员）列出待审核的 skill 发布申请。

---

#### POST `/api/skills/publish-requests/:request_id/review`
（管理员）审核 skill 发布申请。

**请求**
```json
{
  "approved": true,
  "feedback": "xxx"
}
```

---

### 2.3 Tools（工具）

#### GET `/api/tools`
列出工具。

---

#### POST `/api/tools`
创建工具（`execution_type` 只能是 `api` 或 `mcp`）。

**请求**
```json
{
  "name": "my-tool",
  "display_name": "My Tool",
  "execution_type": "api",
  "endpoint": "https://example.com/tool",
  "description": "xxx"
}
```

---

#### PUT `/api/tools/:tool_id`
更新工具。

---

#### DELETE `/api/tools/:tool_id`
删除工具。

---

### 2.4 Knowledge Bases（知识库管理）

#### GET `/api/kbs`
（admin）列出所有知识库。

---

#### POST `/api/kbs`
（admin）创建知识库。

---

#### PUT `/api/kbs/:kb_id`
更新知识库配置。

---

#### DELETE `/api/kbs/:kb_id`
删除知识库。

---

### 2.5 MCP Servers（用户 MCP 服务）

#### GET `/api/mcp-servers`
列出 MCP servers。

---

#### GET `/api/mcp-servers/:mcp_id`
获取单个 MCP server 配置。

---

#### POST `/api/mcp-servers`
注册全局 MCP server（admin）。

---

#### POST `/api/mcp-servers/mine`
用户同步自己的私有 MCP server。

**请求**
```json
{
  "server_type": "stdio|sse",
  "command": "python -m mcp_server",
  "args": [],
  "env": {}
}
```

---

#### PUT `/api/mcp-servers/:mcp_id`
更新 MCP server。

---

#### PATCH `/api/mcp-servers/:mcp_id/enable`
启用/禁用 MCP server。

**请求**
```json
{
  "enabled": true
}
```

---

#### DELETE `/api/mcp-servers/:mcp_id`
删除 MCP server。

---

#### POST `/api/mcp-servers/:mcp_id/sync`
主动同步 MCP server（发现新工具）。

---

### 2.6 Team（Agent 团队）

#### GET `/api/teams`
列出 agent teams。

---

#### POST `/api/teams`
创建 agent team（一个 agent 配多个成员，支持接力）。

**请求**
```json
{
  "agent_id": 1,
  "strategy": "round_robin",
  "max_subtasks": 5
}
```

---

#### PUT `/api/teams/:team_id`
更新 team 配置。

---

#### POST `/api/teams/:team_id/members`
添加成员到 team。

**请求**
```json
{
  "member_agent_id": 2,
  "role": "worker"
}
```

---

#### DELETE `/api/teams/:team_id/members/:member_id`
移除成员。

---

---

## 3. lea-rag (记忆与检索，`:8300`)

### 3.1 知识库检索

#### POST `/kb/search`
全文搜索知识库。

**请求**
```json
{
  "kb_id": "kb-123",
  "query": "什么是 xxx",
  "limit": 10
}
```

**响应**
```json
[
  {
    "doc_id": "doc-456",
    "title": "文档标题",
    "content": "摘录...",
    "score": 0.95
  }
]
```

---

#### POST `/kb/sections`
获取知识库的章节结构（大纲）。

**请求**
```json
{
  "kb_id": "kb-123"
}
```

**响应**
```json
[
  {
    "section_id": "sec-1",
    "title": "第一章",
    "children": []
  }
]
```

---

#### POST `/kb/create`
创建知识库。

**请求**
```json
{
  "name": "My KB",
  "description": "xxx",
  "lang": "zh"
}
```

---

#### GET `/kb/list`
列出知识库。

---

#### GET `/kb/:kb_id/config`
获取知识库配置。

---

#### PUT `/kb/:kb_id/config`
更新知识库配置。

---

#### POST `/kb/:kb_id/upload`
上传文档到知识库。

**请求** — multipart/form-data
- `document` — 文件
- `title` — 文档标题（可选，默认用文件名）

**响应**
```json
{
  "doc_id": "doc-789",
  "title": "文档标题",
  "status": "processing"
}
```

---

#### GET `/kb/:kb_id/documents`
列出知识库中的文档。

---

#### GET `/kb/:kb_id/documents/:doc_id`
获取单个文档详情。

---

#### DELETE `/kb/:kb_id/documents/:doc_id`
删除文档。

---

### 3.2 对话记忆

#### POST `/memory/add`
添加一条记忆。

**请求**
```json
{
  "agent_id": 1,
  "content": "用户说...",
  "tags": ["user-preference", "background"]
}
```

---

#### POST `/memory/search`
搜索相关记忆。

**请求**
```json
{
  "agent_id": 1,
  "query": "用户喜欢...",
  "limit": 5
}
```

**响应**
```json
[
  {
    "id": "mem-123",
    "content": "xxx",
    "created_at": "2026-09-14T10:00:00+08:00"
  }
]
```

---

#### GET `/memory/list`
列出该 agent 的所有记忆。

---

#### PUT `/memory/:memory_id`
更新记忆。

---

#### DELETE `/memory/:memory_id`
删除记忆。

---

#### DELETE `/memory/user/:user_id`
（admin）清空某个用户的全部记忆。

---

#### POST `/memory/extract`
从文本中自动提取和保存记忆。

**请求**
```json
{
  "agent_id": 1,
  "text": "对话历史或文本"
}
```

---

#### GET `/memory/profile`
获取用户的记忆画像（汇总）。

---

#### POST `/memory/profile/merge`
合并多条记忆成一条。

**请求**
```json
{
  "memory_ids": ["mem-1", "mem-2"],
  "merged_content": "xxx"
}
```

---

---

## 4. lea-tools (工具执行与沙箱，`:8400`)

### 4.1 工具执行

#### POST `/tools/execute`
执行一个工具。

**请求**
```json
{
  "tool_name": "curl",
  "params": {
    "url": "https://example.com",
    "method": "GET"
  },
  "session_id": "sess-123"
}
```

**响应**
```json
{
  "status": "success",
  "result": {
    "status_code": 200,
    "body": "..."
  },
  "error": null,
  "usage": {
    "calls": 1
  }
}
```

**权限** — 需要 `X-Tenant-ID`, `X-User-ID`（可选），`X-User-Role`，`X-Agent-ID`（可选）

> 注：`X-User-ID` 缺省时退化为只能用平台工具。

---

### 4.2 文件操作

#### GET `/api/files/list`
列出会话的工作区文件。

**查询参数**
- `session_id` — 必填

**响应**
```json
{
  "files": [
    {
      "path": "hello.txt",
      "name": "hello.txt",
      "dir": "",
      "type": "txt",
      "size": 13,
      "mtime": 1694688000000,
      "in_output": false,
      "status": "draft"
    }
  ],
  "store": "nfs"
}
```

---

#### GET `/api/files/read`
读取工作区文件内容。

**查询参数**
- `session_id` — 必填
- `path` — 文件路径（必填）

**响应**
```json
{
  "path": "hello.txt",
  "content": "file content here"
}
```

---

#### POST `/api/files/write`
写入工作区文件。

**请求**
```json
{
  "session_id": "sess-123",
  "path": "hello.txt",
  "content": "new content"
}
```

**响应** `204 No Content`

---

#### POST `/api/files/edit`
编辑工作区文件（部分替换）。

**请求**
```json
{
  "session_id": "sess-123",
  "path": "hello.txt",
  "search": "old text",
  "replace": "new text"
}
```

---

#### GET `/api/files/download`
下载文件。

**查询参数**
- `session_id` — 必填
- `path` — 文件路径（必填）

**响应** — 文件流

---

#### POST `/api/files/skills/provision`
投递 skills 进沙箱（内部调用）。

---

#### POST `/api/uploads/blob`
上传文件到对象存储（S3）。

**请求** — multipart/form-data
- `file` — 文件二进制
- `session_id` — 会话 ID

**响应**
```json
{
  "key": "uploads/tenant1/sha256.ext",
  "url": "s3://..."
}
```

---

#### GET `/api/uploads/blob`
下载上传的文件。

**查询参数**
- `key` — S3 对象键

**响应** — 文件流

---

#### POST `/api/uploads/stage`
将上传的文件物化进会话目录。

**请求**
```json
{
  "key": "uploads/tenant1/sha256.ext",
  "session_id": "sess-123",
  "dest_path": "imports/document.pdf"
}
```

---

#### POST `/api/artifacts/publish`
发布生成的文件到对象存储。

**请求**
```json
{
  "session_id": "sess-123",
  "path": "output/report.pdf"
}
```

**响应**
```json
{
  "key": "artifacts/tenant1/sha256.pdf",
  "url": "s3://..."
}
```

---

#### DELETE `/api/files/blob`
删除对象存储中的文件。

**查询参数**
- `key` — S3 对象键

---

### 4.3 沙箱会话管理

#### POST `/sandbox/sessions`
创建沙箱会话。

**请求**
```json
{
  "session_id": "sess-123",
  "skill_id": null,
  "user_id": "user-1",
  "tenant_id": "t1",
  "skills": [
    {
      "name": "weather-toolkit",
      "bundle_uri": "s3://..."
    }
  ]
}
```

**响应**
```json
{
  "sandbox_session_id": "sandbox-456",
  "sandbox_id": "sandbox-1"
}
```

---

#### GET `/sandbox/sessions/:session_id`
查询沙箱会话状态。

**响应**
```json
{
  "id": "sandbox-456",
  "status": "running|paused|terminated",
  "created_at": "2026-09-14T10:00:00+08:00"
}
```

---

#### POST `/sandbox/sessions/:session_id/exec`
在沙箱中执行代码。

**请求**
```json
{
  "language": "python|bash",
  "code": "print('Hello')"
}
```

**响应**
```json
{
  "status": "success",
  "stdout": "Hello\n",
  "stderr": "",
  "exit_code": 0,
  "duration_ms": 125
}
```

---

#### POST `/sandbox/sessions/:session_id/upload`
上传文件到沙箱。

**请求** — multipart/form-data
- `file` — 文件二进制
- `dest_path` — 目标路径（相对于 workspace）

---

#### GET `/sandbox/sessions/:session_id/download`
从沙箱下载文件。

**查询参数**
- `path` — 文件路径

**响应** — 文件流

---

#### DELETE `/sandbox/sessions/:session_id`
删除沙箱会话。

---

### 4.4 会话能力查询

#### POST `/session/capabilities`
获取该 session 能用的工具列表。

**请求**
```json
{
  "tenant_id": "t1",
  "user_id": "user-1",
  "role": "user",
  "agent_id": 1
}
```

**响应**
```json
{
  "tools": [
    {
      "name": "curl",
      "description": "HTTP client",
      "params": {}
    }
  ],
  "ttl": 300,
  "cached": false
}
```

---

### 4.5 MCP（Model Context Protocol）

#### POST `/mcp/discover`
发现 MCP server 提供的工具。

**请求**
```json
{
  "server_uri": "stdio://python -m mcp_server",
  "session_id": "sess-123"
}
```

**响应**
```json
{
  "tools": [
    {
      "name": "tool1",
      "description": "xxx",
      "params": {}
    }
  ]
}
```

---

#### POST `/mcp/sync`
（admin）同步 MCP server 工具到库。

---

---

## 5. 通用约定

### 身份信息注入

所有服务都信任以下请求头（由 gateway 或调用方注入）：

```
X-Tenant-ID: t1
X-User-ID: user-1
X-User-Role: user|admin|platform_admin
X-Agent-ID: 1  (可选)
```

**不使用 JWT**，这些头的内容完全取决于调用方。

### 错误响应

```json
{
  "error": "description",
  "status": 400
}
```

常见状态码：
- `400` — 请求格式错、参数缺失
- `403` — 权限不足（基于 X-User-Role、归属等）
- `404` — 资源不存在
- `409` — 冲突（比如重复创建）
- `429` — 速率限制
- `500` — 服务器错误
- `503` — 依赖服务不可用

### 时间格式

RFC3339 + 纳秒 + 时区偏移，示例：
```
2026-09-14T10:00:00.123456789+08:00
```

### 分页

支持 `limit` 和 `offset`:
```
GET /api/sessions?limit=20&offset=0
```

响应包含 `total` 字段：
```json
{
  "items": [...],
  "total": 150
}
```

---

## 6. 端口汇总

| 服务 | 端口 | 用途 |
|------|------|------|
| lea-agent | 8100 | 对话引擎、会话管理 |
| lea-config | 8500 | Agent/Skill/Tool/KB 配置 |
| lea-rag | 8300 | 检索和记忆 |
| lea-tools | 8400 | 工具执行、沙箱、文件 |

---

## 7. 集成示例

### Python 调用示例

```python
import requests
import json

BASE_URL = "http://127.0.0.1:8100"

# 创建会话
resp = requests.post(
    f"{BASE_URL}/api/sessions",
    json={"agent_id": 0},
    headers={
        "X-Tenant-ID": "t1",
        "X-User-ID": "user-1",
        "X-User-Role": "user"
    }
)
session_id = resp.json()["id"]

# 发送消息（SSE 流式）
with requests.post(
    f"{BASE_URL}/api/chat/stream",
    json={"session_id": session_id, "message": "你好"},
    headers={"X-Tenant-ID": "t1", "X-User-ID": "user-1", "X-User-Role": "user"},
    stream=True
) as resp:
    for line in resp.iter_lines():
        if line.startswith(b"event:"):
            event_type = line.decode().split(":", 1)[1].strip()
        elif line.startswith(b"data:"):
            data = line.decode().split(":", 1)[1].strip()
            print(f"{event_type}: {data}")

# 列出文件
resp = requests.get(
    f"{BASE_URL}/api/files/list",
    params={"session_id": session_id},
    headers={"X-Tenant-ID": "t1", "X-User-ID": "user-1", "X-User-Role": "user"}
)
files = resp.json()["files"]
print(json.dumps(files, indent=2))
```

---

## 8. 认证扩展（未来规划）

目前所有身份信息通过请求头传递。未来可能引入：
- **API Key** —— 为第三方应用生成长期 key
- **OAuth2** —— 标准委托授权流
- **请求签名** —— 基于 HMAC 的请求签名（类似 AWS Signature V4）

暂时不实现，保持极简。
