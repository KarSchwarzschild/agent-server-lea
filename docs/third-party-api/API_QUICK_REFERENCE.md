# LEA Platform API 快速参考

**版本**: 1.0.0  
**适用对象**: API 集成开发者

---

## 📌 核心信息

| 项目 | 值 |
|------|-----|
| **API Base URL** | `http://localhost:8100/api` (开发) / `https://api.lea-platform.com/api` (生产) |
| **认证方式** | Bearer Token |
| **请求格式** | JSON |
| **响应格式** | JSON |
| **编码** | UTF-8 |
| **限流** | 100 req/min per user |
| **超时** | 建议 60 秒 |

---

## 🔐 认证

### 使用 Token 的方式

```bash
# cURL
curl -H "Authorization: Bearer YOUR_TOKEN" http://localhost:8100/api/sessions

# Python
headers = {"Authorization": f"Bearer {token}"}

# JavaScript
headers = {"Authorization": `Bearer ${token}`}
```

---

## 📋 核心端点速查表

### 会话管理

```bash
# 创建会话
POST /sessions
Body: { "user_id": "user_123", "agent_id": 0, "title": "Chat Title" }

# 获取会话列表
GET /sessions?page=1&page_size=20

# 获取单个会话
GET /sessions/{session_id}

# 获取会话消息
GET /sessions/{session_id}/messages?limit=50&offset=0

# 切换专家（Layer 1）
PUT /sessions/{session_id}/active-agent
Body: { "agent_id": 42 }

# 删除会话
DELETE /sessions/{session_id}
```

### 聊天

```bash
# 发送消息（流式）⭐ 推荐
POST /chat/stream
Body: { "session_id": "sess_abc", "content": "Hello" }
Response: Server-Sent Events (SSE)

# 获取聊天状态
GET /chat/status/{session_id}

# 停止生成
POST /chat/stop/{session_id}
```

### 会话挂载（工具/知识库）

```bash
# 添加挂载
POST /sessions/{session_id}/attachments
Body: { "kind": "tool", "ref_id": "tool_123" }

# 获取挂载列表
GET /sessions/{session_id}/attachments

# 移除挂载
DELETE /sessions/{session_id}/attachments/{kind}/{ref_id}
```

### 文件管理

```bash
# 列出用户文件
GET /user/files

# 获取存储信息
GET /user/storage

# 删除用户文件
DELETE /user/files/{kind}/{file_id}

# 导入文件到会话
POST /sessions/{session_id}/files/import
Body: { "file_id": "file_123" }

# 发布会话文件
POST /sessions/{session_id}/files/publish
Body: { "session_file_id": "sess_file_456", "filename": "report.pdf" }
```

### 项目管理

```bash
# 创建项目
POST /projects
Body: { "name": "My Project", "description": "..." }

# 获取项目列表
GET /projects

# 更新项目
PUT /projects/{project_id}
Body: { "name": "New Name", "description": "..." }

# 归档项目
POST /projects/{project_id}/archive

# 删除项目
DELETE /projects/{project_id}
```

---

## 💬 SSE 流式事件类型

当调用 `/chat/stream` 时，会收到以下事件：

```json
// 开始事件
{"id":"evt_1","type":"start","timestamp":"2026-09-15T10:32:00Z"}

// 内容事件（增量）
{"id":"evt_2","type":"content","content":"机器学习是","timestamp":"2026-09-15T10:32:01Z"}

// 结束事件
{"id":"evt_5","type":"end","timestamp":"2026-09-15T10:32:05Z"}

// 错误事件
{"id":"evt_6","type":"error","error":"Error message","timestamp":"2026-09-15T10:32:06Z"}
```

### 处理 SSE 流

**Python**:
```python
response = requests.post(url, stream=True, headers=headers)
for line in response.iter_lines():
    if line.startswith(b"data: "):
        event = json.loads(line[6:])
        if event["type"] == "content":
            print(event["content"], end="")
```

**JavaScript**:
```javascript
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
      if (event.type === "content") console.log(event.content);
    }
  }
}
```

---

## 🔄 常见工作流

### 工作流 1: 完整对话流程

```
1. POST /sessions              → 创建会话，获得 session_id
2. POST /chat/stream           → 发送首条消息，流式接收回复
3. POST /chat/stream           → 发送第二条消息
4. ...                         → 重复步骤 3
5. DELETE /sessions/{id}       → 删除会话
```

### 工作流 2: 使用专家（Layer 1）

```
1. POST /sessions (agent_id=42)       → 创建会话时指定专家
2. POST /chat/stream                  → 用该专家回答
3. PUT /sessions/{id}/active-agent    → 切换到另一个专家
4. POST /chat/stream                  → 用新专家回答
5. PUT /sessions/{id}/active-agent (agent_id=0) → 回到默认
```

### 工作流 3: 使用工具/知识库

```
1. POST /sessions                     → 创建会话
2. POST /sessions/{id}/attachments    → 挂载工具 (kind=tool)
3. POST /sessions/{id}/attachments    → 挂载知识库 (kind=kb)
4. POST /chat/stream                  → 发送消息（AI 可访问这些资源）
5. DELETE /sessions/{id}/attachments  → 移除挂载
```

---

## ⚠️ 错误处理

### HTTP 状态码

| 状态码 | 说明 | 处理方式 |
|--------|------|---------|
| `200` | 成功 | 正常处理 |
| `400` | 请求参数错误 | 检查请求体和参数 |
| `401` | 未授权（缺少/失效 token） | 检查 token 或重新获取 |
| `403` | 禁止访问（无权限） | 检查用户权限 |
| `404` | 资源不存在 | 检查 ID 是否正确 |
| `409` | 冲突（归属不匹配） | 检查用户和会话关联 |
| `429` | 限流（请求过于频繁） | 实现指数退避重试 |
| `500` | 服务器错误 | 稍后重试或联系支持 |
| `503` | 服务暂时不可用 | 稍后重试 |

### 错误响应格式

```json
{
  "code": 400,
  "message": "Bad Request",
  "error": {
    "field": "session_id",
    "reason": "Invalid format"
  }
}
```

### 重试策略

```python
import time

def retry_request(func, max_retries=3):
    for attempt in range(max_retries):
        try:
            return func()
        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                raise
            wait_time = 2 ** attempt  # 指数退避
            print(f"Retrying in {wait_time}s...")
            time.sleep(wait_time)

# 使用
response = retry_request(
    lambda: requests.post(url, json=data, headers=headers)
)
```

---

## 🚀 一行命令快速测试

### 创建会话
```bash
curl -X POST http://localhost:8100/api/sessions \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"user_123","title":"Test"}'
```

### 发送消息
```bash
curl -X POST http://localhost:8100/api/chat/stream \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"sess_abc123","content":"Hello"}' \
  -N
```

### 切换专家
```bash
curl -X PUT http://localhost:8100/api/sessions/sess_abc123/active-agent \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent_id":42}'
```

### 获取会话详情
```bash
curl http://localhost:8100/api/sessions/sess_abc123 \
  -H "Authorization: Bearer YOUR_TOKEN"
```

---

## 📚 完整文档位置

| 文档 | 用途 |
|------|------|
| `API_SPECIFICATION.md` | 📖 详细的 API 文档 |
| `openapi.yaml` | 🔧 OpenAPI 3.0 规范（机器可读） |
| `POSTMAN_COLLECTION.json` | 📮 Postman 集合（直接导入测试） |
| `THIRD_PARTY_INTEGRATION_GUIDE.md` | 🚀 集成快速指南 + 代码示例 |
| `API_QUICK_REFERENCE.md` | ⚡ 本文档（速查表） |

---

## 🔑 Key Points（必记）

1. **Token 安全**: 永远不要在代码中硬编码 token
2. **流式 API**: 使用 `/chat/stream` 获得最佳性能和实时体验
3. **错误处理**: 实现重试机制处理临时失败（429/5xx）
4. **超时设置**: 流式请求可能耗时较长，建议 60 秒超时
5. **限流**: 100 req/min 的限制，超过返回 429
6. **归属验证**: API 自动验证 session 属于请求的 user，不能访问他人的 session
7. **Layer 1 功能**: 通过 `active_agent_id` 可动态切换 AI 人格

---

## 💡 常见代码片段

### 初始化客户端

```python
import requests
from typing import Iterator

class LEAClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
    
    def request(self, method, endpoint, **kwargs):
        url = f"{self.base_url}{endpoint}"
        response = requests.request(method, url, headers=self.headers, **kwargs)
        response.raise_for_status()
        return response.json()

client = LEAClient("http://localhost:8100/api", "your_token")
```

### 流式处理

```python
def stream_chat(client, session_id, content):
    response = requests.post(
        f"{client.base_url}/chat/stream",
        json={"session_id": session_id, "content": content},
        headers=client.headers,
        stream=True
    )
    
    for line in response.iter_lines():
        if line.startswith(b"data: "):
            import json
            event = json.loads(line[6:])
            if event.get("type") == "content":
                yield event.get("content")

# 使用
for chunk in stream_chat(client, "sess_123", "Hello"):
    print(chunk, end="", flush=True)
```

---

## 📞 需要帮助？

- 📖 查看完整文档: `docs/API_SPECIFICATION.md`
- 🔧 导入 Postman: `docs/POSTMAN_COLLECTION.json`
- 🚀 集成指南: `docs/THIRD_PARTY_INTEGRATION_GUIDE.md`
- 💬 OpenAPI 规范: `docs/openapi.yaml`
- 📧 联系支持: support@lea-platform.com

---

**最后更新**: 2026-09-15  
**版本**: 1.0.0
