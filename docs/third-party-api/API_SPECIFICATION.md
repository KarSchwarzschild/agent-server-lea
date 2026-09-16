# LEA Platform API 文档

**版本**: 1.0.0  
**生成日期**: 2026-09-15  
**适用对象**: 第三方平台集成 / 外部开发者

---

## 📋 文档目录

1. [认证](#认证)
2. [基础信息](#基础信息)
3. [会话管理](#会话管理)
4. [聊天接口](#聊天接口)
5. [项目管理](#项目管理)
6. [文件管理](#文件管理)
7. [错误处理](#错误处理)
8. [示例代码](#示例代码)

---

## 认证

### 认证方式：Bearer Token

所有 API 请求都需要在 Header 中提供 Authorization token。

```bash
Authorization: Bearer YOUR_TOKEN_HERE
```

**获取 Token 方式**：
- 联系 LEA Platform 管理员获取
- 或在系统设置中生成 API Key

---

## 基础信息

### API 基础 URL

```
生产环境: https://api.lea-platform.com/api
测试环境: http://localhost:8100/api
```

### 请求/响应格式

- **Content-Type**: `application/json`
- **编码**: UTF-8
- **时间格式**: ISO 8601 (例如: `2026-09-15T10:30:00Z`)

### 通用响应格式

所有响应都遵循以下格式：

#### 成功响应（200 OK）

```json
{
  "code": 0,
  "message": "success",
  "data": {
    // 具体业务数据
  }
}
```

#### 错误响应

```json
{
  "code": 400,
  "message": "Bad Request",
  "error": {
    "field": "specific error details"
  }
}
```

---

## 会话管理

### 1. 创建会话

创建一个新的聊天会话。

```
POST /sessions
```

#### 请求体

```json
{
  "user_id": "user_123",
  "project_id": "proj_456",
  "agent_id": 0,
  "title": "新对话"
}
```

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `user_id` | string | ✅ | 用户 ID |
| `project_id` | string | ❌ | 项目 ID（可选） |
| `agent_id` | integer | ❌ | Agent ID，0=默认 superagent，其他值=指定专家 |
| `title` | string | ❌ | 会话标题 |

#### 响应

```json
{
  "code": 0,
  "data": {
    "id": "sess_abc123",
    "user_id": "user_123",
    "project_id": "proj_456",
    "active_agent_id": 0,
    "title": "新对话",
    "created_at": "2026-09-15T10:30:00Z",
    "updated_at": "2026-09-15T10:30:00Z"
  }
}
```

---

### 2. 获取会话列表

```
GET /sessions?page=1&page_size=20
```

#### 查询参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `page` | integer | 页码（默认 1） |
| `page_size` | integer | 每页数量（默认 20，最大 100） |
| `project_id` | string | 过滤指定项目 |

#### 响应

```json
{
  "code": 0,
  "data": {
    "items": [
      {
        "id": "sess_abc123",
        "user_id": "user_123",
        "active_agent_id": 0,
        "title": "新对话",
        "created_at": "2026-09-15T10:30:00Z",
        "updated_at": "2026-09-15T10:30:00Z"
      }
    ],
    "total": 42,
    "page": 1,
    "page_size": 20
  }
}
```

---

### 3. 获取单个会话

```
GET /sessions/{session_id}
```

#### 路径参数

| 参数 | 说明 |
|------|------|
| `session_id` | 会话 ID |

#### 响应

```json
{
  "code": 0,
  "data": {
    "id": "sess_abc123",
    "user_id": "user_123",
    "project_id": "proj_456",
    "active_agent_id": 42,
    "title": "新对话",
    "created_at": "2026-09-15T10:30:00Z",
    "updated_at": "2026-09-15T10:30:00Z",
    "message_count": 5
  }
}
```

---

### 4. 删除会话

```
DELETE /sessions/{session_id}
```

#### 响应

```json
{
  "code": 0,
  "message": "Session deleted successfully"
}
```

---

### 5. 获取会话消息

```
GET /sessions/{session_id}/messages?limit=50&offset=0
```

#### 查询参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `limit` | integer | 返回消息数（默认 50，最大 100） |
| `offset` | integer | 偏移量（默认 0） |

#### 响应

```json
{
  "code": 0,
  "data": {
    "messages": [
      {
        "id": "msg_123",
        "session_id": "sess_abc123",
        "role": "user",
        "content": "你好",
        "created_at": "2026-09-15T10:31:00Z"
      },
      {
        "id": "msg_124",
        "session_id": "sess_abc123",
        "role": "assistant",
        "content": "你好！有什么可以帮助你的吗？",
        "created_at": "2026-09-15T10:31:05Z"
      }
    ],
    "total": 12
  }
}
```

---

### 6. 切换激活 Agent（专家切换）

切换会话使用的 Agent。agent_id=0 表示回到默认 superagent。

```
PUT /sessions/{session_id}/active-agent
```

#### 请求体

```json
{
  "agent_id": 42
}
```

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `agent_id` | integer | ✅ | Agent ID，0=回到默认 |

#### 响应

```json
{
  "code": 0,
  "data": {
    "success": true,
    "applies": "next_message",
    "agent_id": 42
  }
}
```

**applies 字段说明**:
- `"next_message"`: 下一条用户消息时立即生效
- `"after_current_turn"`: 当前对话轮次完成后生效

#### 错误响应

| 状态码 | 说明 |
|--------|------|
| `400` | agent_id 无效或格式错误 |
| `403` | 无权限访问该 agent |
| `404` | 会话不存在 |
| `409` | 会话不属于当前用户 |

---

## 聊天接口

### 1. 发送消息（流式响应）

发送消息并以 SSE（Server-Sent Events）流式接收 AI 回复。这是推荐的主链路。

```
POST /chat/stream
```

#### 请求体

```json
{
  "session_id": "sess_abc123",
  "content": "请解释什么是机器学习",
  "agent_id": 0
}
```

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `session_id` | string | ✅ | 会话 ID |
| `content` | string | ✅ | 消息内容 |
| `agent_id` | integer | ❌ | 可选覆盖会话的激活 agent |

#### 响应（SSE 流）

```
data: {"id":"evt_1","type":"start","timestamp":"2026-09-15T10:32:00Z"}
data: {"id":"evt_2","type":"content","content":"机器学习是","timestamp":"2026-09-15T10:32:01Z"}
data: {"id":"evt_3","type":"content","content":"人工智能","timestamp":"2026-09-15T10:32:02Z"}
data: {"id":"evt_4","type":"content","content":"的一个分支"}
data: {"id":"evt_5","type":"end","timestamp":"2026-09-15T10:32:05Z"}
```

**事件类型**:
- `start`: 开始生成
- `content`: 增量内容
- `end`: 生成完毕
- `error`: 发生错误

#### 示例（JavaScript）

```javascript
const response = await fetch('http://localhost:8100/api/chat/stream', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Authorization': 'Bearer YOUR_TOKEN'
  },
  body: JSON.stringify({
    session_id: 'sess_abc123',
    content: '请解释什么是机器学习'
  })
});

const reader = response.body.getReader();
const decoder = new TextDecoder();

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  
  const text = decoder.decode(value);
  const lines = text.split('\n');
  
  for (const line of lines) {
    if (line.startsWith('data: ')) {
      const event = JSON.parse(line.slice(6));
      console.log('Event:', event);
    }
  }
}
```

---

### 2. 获取聊天状态

获取当前会话的对话轮次状态。

```
GET /chat/status/{session_id}
```

#### 响应

```json
{
  "code": 0,
  "data": {
    "session_id": "sess_abc123",
    "is_generating": false,
    "last_message_id": "msg_124",
    "total_messages": 12
  }
}
```

---

### 3. 停止生成

停止当前的 AI 生成过程。

```
POST /chat/stop/{session_id}
```

#### 响应

```json
{
  "code": 0,
  "message": "Generation stopped"
}
```

---

### 4. 上传附件

上传文件、图像等附件到会话。

```
POST /chat/attachments/upload
```

#### 请求（multipart/form-data）

| 字段 | 类型 | 说明 |
|------|------|------|
| `file` | binary | 文件内容 |
| `session_id` | string | 会话 ID |
| `description` | string | 文件描述（可选） |

#### 响应

```json
{
  "code": 0,
  "data": {
    "attachment_id": "att_xyz789",
    "filename": "document.pdf",
    "size": 1024000,
    "mime_type": "application/pdf",
    "created_at": "2026-09-15T10:33:00Z"
  }
}
```

#### 限制

- 单文件最大 100 MB
- 支持格式：PDF、图像、文本、视频等（详见配置）

---

### 5. 下载附件

```
GET /chat/attachments/{attachment_id}/download
```

#### 响应

返回文件的二进制内容，Content-Type 根据文件类型自动设置。

---

### 6. 会话挂载（临时添加工具/知识库）

在**这一个会话里**临时挂上自己的工具/知识库，不动 agent 的绑定。

```
POST /sessions/{session_id}/attachments
```

#### 请求体

```json
{
  "kind": "tool",
  "ref_id": "tool_123"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `kind` | string | 类型：`tool` / `skill` / `kb` / `mcp_server` |
| `ref_id` | string | 引用的工具/技能/知识库 ID |

#### 响应

```json
{
  "code": 0,
  "data": {
    "id": "att_sess_123",
    "session_id": "sess_abc123",
    "kind": "tool",
    "ref_id": "tool_123",
    "created_at": "2026-09-15T10:34:00Z"
  }
}
```

---

### 7. 获取会话挂载列表

```
GET /sessions/{session_id}/attachments
```

#### 响应

```json
{
  "code": 0,
  "data": {
    "attachments": [
      {
        "id": "att_sess_123",
        "kind": "tool",
        "ref_id": "tool_123",
        "name": "Python 执行器",
        "created_at": "2026-09-15T10:34:00Z"
      }
    ]
  }
}
```

---

### 8. 取消会话挂载

```
DELETE /sessions/{session_id}/attachments/{kind}/{ref_id}
```

#### 响应

```json
{
  "code": 0,
  "message": "Attachment removed"
}
```

---

## 项目管理

### 1. 创建项目

```
POST /projects
```

#### 请求体

```json
{
  "name": "我的项目",
  "description": "项目描述"
}
```

#### 响应

```json
{
  "code": 0,
  "data": {
    "id": "proj_123",
    "name": "我的项目",
    "description": "项目描述",
    "created_at": "2026-09-15T10:35:00Z"
  }
}
```

---

### 2. 获取项目列表

```
GET /projects
```

#### 响应

```json
{
  "code": 0,
  "data": {
    "projects": [
      {
        "id": "proj_123",
        "name": "我的项目",
        "created_at": "2026-09-15T10:35:00Z"
      }
    ]
  }
}
```

---

### 3. 更新项目

```
PUT /projects/{project_id}
```

#### 请求体

```json
{
  "name": "更新的项目名",
  "description": "更新的描述"
}
```

#### 响应

```json
{
  "code": 0,
  "data": {
    "id": "proj_123",
    "name": "更新的项目名",
    "description": "更新的描述",
    "updated_at": "2026-09-15T10:36:00Z"
  }
}
```

---

### 4. 归档项目

```
POST /projects/{project_id}/archive
```

#### 响应

```json
{
  "code": 0,
  "message": "Project archived"
}
```

---

### 5. 删除项目

```
DELETE /projects/{project_id}
```

#### 响应

```json
{
  "code": 0,
  "message": "Project deleted"
}
```

---

## 文件管理

### 1. 列出用户文件

```
GET /user/files
```

#### 响应

```json
{
  "code": 0,
  "data": {
    "files": [
      {
        "id": "file_123",
        "name": "report.pdf",
        "size": 2048000,
        "kind": "upload",
        "created_at": "2026-09-15T10:37:00Z"
      }
    ]
  }
}
```

---

### 2. 获取用户存储信息

```
GET /user/storage
```

#### 响应

```json
{
  "code": 0,
  "data": {
    "used": 5242880,
    "total": 10737418240,
    "used_percent": 0.05
  }
}
```

---

### 3. 删除用户文件

```
DELETE /user/files/{kind}/{file_id}
```

#### 路径参数

| 参数 | 说明 |
|------|------|
| `kind` | 文件类型：`upload` 或 `artifact` |
| `file_id` | 文件 ID |

#### 响应

```json
{
  "code": 0,
  "message": "File deleted"
}
```

---

### 4. 导入文件到会话

```
POST /sessions/{session_id}/files/import
```

#### 请求体

```json
{
  "file_id": "file_123"
}
```

#### 响应

```json
{
  "code": 0,
  "data": {
    "session_file_id": "sess_file_456"
  }
}
```

---

### 5. 发布会话文件

```
POST /sessions/{session_id}/files/publish
```

#### 请求体

```json
{
  "session_file_id": "sess_file_456",
  "filename": "final_report.pdf"
}
```

#### 响应

```json
{
  "code": 0,
  "data": {
    "artifact_id": "artifact_789",
    "filename": "final_report.pdf",
    "url": "https://..."
  }
}
```

---

## 错误处理

### 错误响应格式

```json
{
  "code": 400,
  "message": "Bad Request",
  "error": {
    "field": "session_id",
    "reason": "Invalid session ID format"
  }
}
```

### 常见错误码

| 状态码 | 说明 |
|--------|------|
| `200` | 请求成功 |
| `400` | 请求参数错误 |
| `401` | 未授权（缺少 token） |
| `403` | 禁止访问（无权限） |
| `404` | 资源不存在 |
| `409` | 冲突（如会话不属于当前用户） |
| `429` | 请求过于频繁（限流） |
| `500` | 服务器内部错误 |
| `503` | 服务暂时不可用 |

---

## 示例代码

### Python 示例

```python
import requests
import json

BASE_URL = "http://localhost:8100/api"
TOKEN = "your_token_here"

headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json"
}

# 创建会话
response = requests.post(
    f"{BASE_URL}/sessions",
    json={
        "user_id": "user_123",
        "title": "我的第一个对话"
    },
    headers=headers
)
session = response.json()["data"]
session_id = session["id"]
print(f"创建会话: {session_id}")

# 发送消息（流式）
response = requests.post(
    f"{BASE_URL}/chat/stream",
    json={
        "session_id": session_id,
        "content": "你好，请自我介绍一下"
    },
    headers=headers,
    stream=True
)

for line in response.iter_lines():
    if line:
        if line.startswith(b"data: "):
            event = json.loads(line[6:])
            if event.get("type") == "content":
                print(event.get("content"), end="", flush=True)
```

### JavaScript 示例

```javascript
const BASE_URL = "http://localhost:8100/api";
const TOKEN = "your_token_here";

const headers = {
  "Authorization": `Bearer ${TOKEN}`,
  "Content-Type": "application/json"
};

// 创建会话
const createSession = async () => {
  const response = await fetch(`${BASE_URL}/sessions`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      user_id: "user_123",
      title: "我的第一个对话"
    })
  });
  
  const data = await response.json();
  return data.data.id;
};

// 发送消息（流式）
const sendMessage = async (sessionId, content) => {
  const response = await fetch(`${BASE_URL}/chat/stream`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      session_id: sessionId,
      content: content
    })
  });

  const reader = response.body.getReader();
  const decoder = new TextDecoder();

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    const text = decoder.decode(value);
    const lines = text.split("\n");

    for (const line of lines) {
      if (line.startsWith("data: ")) {
        const event = JSON.parse(line.slice(6));
        if (event.type === "content") {
          process.stdout.write(event.content);
        }
      }
    }
  }
};

// 使用示例
(async () => {
  const sessionId = await createSession();
  console.log(`创建会话: ${sessionId}`);
  
  await sendMessage(sessionId, "你好，请自我介绍一下");
})();
```

### cURL 示例

```bash
# 创建会话
curl -X POST http://localhost:8100/api/sessions \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user_123",
    "title": "我的第一个对话"
  }'

# 发送消息（流式）
curl -X POST http://localhost:8100/api/chat/stream \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "sess_abc123",
    "content": "你好，请自我介绍一下"
  }' \
  -N

# 切换专家
curl -X PUT http://localhost:8100/api/sessions/sess_abc123/active-agent \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": 42
  }'
```

---

## 最佳实践

### 1. 认证安全

- ✅ 使用 HTTPS 传输 token
- ✅ 定期轮换 token
- ✅ 不要在客户端代码中硬编码 token
- ❌ 不要在 GET 请求中传递 token

### 2. 请求优化

- ✅ 使用流式 API（`/chat/stream`）以获得最佳性能
- ✅ 合理设置请求超时（建议 60 秒）
- ✅ 实现重试逻辑处理临时错误

### 3. 错误处理

```javascript
// 推荐的错误处理模式
try {
  const response = await fetch(url, options);
  
  if (!response.ok) {
    const error = await response.json();
    throw new Error(`API Error: ${error.message}`);
  }
  
  return await response.json();
} catch (error) {
  console.error("请求失败:", error);
  // 实现重试逻辑
}
```

### 4. 速率限制

- API 限流：每个用户 100 请求/分钟
- 当收到 429 响应时，请等待后重试
- 使用指数退避重试策略

---

## 版本历史

### v1.0.0 (2026-09-15)

- ✅ 初始版本发布
- ✅ 会话管理完整 API
- ✅ 聊天流式 API
- ✅ 项目和文件管理
- ✅ 专家切换（Layer 1）功能

---

## 技术支持

- **文档问题**: 提交 GitHub Issues
- **API 问题**: 联系技术支持团队
- **集成帮助**: 查看示例代码或联系销售

---

**文档最后更新**: 2026-09-15
