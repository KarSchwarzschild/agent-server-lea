# LEA Platform 第三方集成快速指南

**版本**: 1.0.0  
**最后更新**: 2026-09-15  
**适用对象**: 希望接入 LEA Platform 服务的第三方开发者

---

## 📚 目录

1. [快速开始](#快速开始)
2. [获取 API Token](#获取-api-token)
3. [集成场景](#集成场景)
4. [常见问题](#常见问题)
5. [技术支持](#技术支持)

---

## 快速开始

### 1. 环境准备

在集成 LEA Platform 前，请确保你已准备好：

- **API Endpoint**: `https://api.lea-platform.com/api` (生产) 或 `http://localhost:8100/api` (开发)
- **认证方式**: Bearer Token (OAuth 2.0)
- **请求格式**: JSON
- **响应格式**: JSON
- **编码**: UTF-8
- **超时设置**: 建议 60 秒

### 2. 最简单的集成示例

#### Python

```python
import requests
import json
from typing import Iterator

class LEAPlatformClient:
    def __init__(self, api_url: str, token: str):
        self.api_url = api_url
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
    
    def create_session(self, user_id: str, title: str = "") -> dict:
        """创建新聊天会话"""
        response = requests.post(
            f"{self.api_url}/sessions",
            json={"user_id": user_id, "title": title},
            headers=self.headers
        )
        response.raise_for_status()
        return response.json()["data"]
    
    def send_message(self, session_id: str, content: str) -> Iterator[str]:
        """发送消息并流式获取响应"""
        response = requests.post(
            f"{self.api_url}/chat/stream",
            json={"session_id": session_id, "content": content},
            headers=self.headers,
            stream=True
        )
        response.raise_for_status()
        
        for line in response.iter_lines():
            if line:
                if line.startswith(b"data: "):
                    event = json.loads(line[6:])
                    if event.get("type") == "content":
                        yield event.get("content", "")

# 使用示例
client = LEAPlatformClient(
    api_url="http://localhost:8100/api",
    token="your_token_here"
)

# 创建会话
session = client.create_session(user_id="user_123", title="My First Chat")
session_id = session["id"]

# 发送消息
for chunk in client.send_message(session_id, "Hello, LEA Platform!"):
    print(chunk, end="", flush=True)
```

#### JavaScript/Node.js

```javascript
class LEAPlatformClient {
  constructor(apiUrl, token) {
    this.apiUrl = apiUrl;
    this.token = token;
  }

  async createSession(userId, title = "") {
    const response = await fetch(`${this.apiUrl}/sessions`, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${this.token}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ user_id: userId, title })
    });
    const data = await response.json();
    return data.data;
  }

  async *sendMessage(sessionId, content) {
    const response = await fetch(`${this.apiUrl}/chat/stream`, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${this.token}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ session_id: sessionId, content })
    });

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      
      for (let i = 0; i < lines.length - 1; i++) {
        const line = lines[i];
        if (line.startsWith("data: ")) {
          const event = JSON.parse(line.slice(6));
          if (event.type === "content") {
            yield event.content;
          }
        }
      }
      buffer = lines[lines.length - 1];
    }
  }
}

// 使用示例
const client = new LEAPlatformClient(
  "http://localhost:8100/api",
  "your_token_here"
);

(async () => {
  const session = await client.createSession("user_123", "My First Chat");
  console.log("Session created:", session.id);
  
  for await (const chunk of client.sendMessage(session.id, "Hello!")) {
    process.stdout.write(chunk);
  }
})();
```

---

## 获取 API Token

### 方式 1：从管理后台生成（推荐）

1. 登录 LEA Platform 管理后台
2. 导航到 **设置** → **API Keys**
3. 点击 **生成新 Token**
4. 复制 Token（安全保管，仅显示一次）
5. 在你的应用中配置环境变量：

```bash
export LEA_API_TOKEN="your_generated_token_here"
```

### 方式 2：联系管理员

- 向 LEA Platform 管理员申请 API Token
- 提供你的组织名称和应用名称
- 管理员将生成并发送 Token

### Token 安全建议

- ✅ **存储在环境变量或密钥管理系统中**
- ✅ **定期轮换 Token（建议每 90 天）**
- ✅ **使用 HTTPS 传输**
- ✅ **在版本控制中使用 .gitignore 排除 token**
- ❌ **不要在代码中硬编码 token**
- ❌ **不要在公开的日志或文档中暴露 token**

---

## 集成场景

### 场景 1：简单的聊天集成

适用于：将 LEA Platform 聊天功能集成到你的应用

```python
def simple_chat_flow():
    client = LEAPlatformClient(API_URL, TOKEN)
    
    # Step 1: 为每个用户创建会话
    session = client.create_session(user_id="user_123")
    
    # Step 2: 用户输入问题
    user_question = "How to integrate LEA Platform?"
    
    # Step 3: 流式获取 AI 回复
    response_text = ""
    for chunk in client.send_message(session["id"], user_question):
        response_text += chunk
        # 实时显示到 UI
    
    return response_text
```

### 场景 2：多轮对话保存历史

适用于：构建完整的对话管理系统

```python
class ChatManager:
    def __init__(self, client, user_id):
        self.client = client
        self.user_id = user_id
        self.session = None
    
    def initialize(self):
        """初始化会话"""
        self.session = self.client.create_session(
            user_id=self.user_id,
            title="My Chat Session"
        )
    
    def send_and_save(self, user_message: str) -> str:
        """发送消息并保存到你的数据库"""
        response = ""
        for chunk in self.client.send_message(
            self.session["id"], 
            user_message
        ):
            response += chunk
        
        # 保存到你的数据库
        self.save_to_db(user_message, response)
        return response
    
    def save_to_db(self, user_msg, ai_response):
        # 实现你的数据库保存逻辑
        pass
    
    def get_history(self, limit=50):
        """获取对话历史"""
        response = requests.get(
            f"{self.client.api_url}/sessions/{self.session['id']}/messages",
            params={"limit": limit},
            headers=self.client.headers
        )
        return response.json()["data"]["messages"]

# 使用示例
manager = ChatManager(client, user_id="user_123")
manager.initialize()
response1 = manager.send_and_save("What is machine learning?")
response2 = manager.send_and_save("Can you explain neural networks?")
history = manager.get_history()
```

### 场景 3：使用专家（Layer 1 功能）

适用于：动态切换 AI 人格和能力

```python
def expert_switching_demo():
    client = LEAPlatformClient(API_URL, TOKEN)
    
    # 创建会话时指定专家
    session = client.create_session(
        user_id="user_123",
        agent_id=42  # 指定专家 ID
    )
    
    # 发送消息（将使用指定的专家）
    response = ""
    for chunk in client.send_message(
        session["id"],
        "As a data scientist, how would you approach this problem?"
    ):
        response += chunk
    
    # 中途切换专家
    switch_response = requests.put(
        f"{API_URL}/sessions/{session['id']}/active-agent",
        json={"agent_id": 99},  # 切换到另一个专家
        headers=client.headers
    )
    
    # 下一条消息将使用新专家
    response2 = ""
    for chunk in client.send_message(
        session["id"],
        "Now, as a product manager, what's your view?"
    ):
        response2 += chunk
    
    # 取消专家，回到默认
    requests.put(
        f"{API_URL}/sessions/{session['id']}/active-agent",
        json={"agent_id": 0},  # 0 表示默认
        headers=client.headers
    )

expert_switching_demo()
```

### 场景 4：批量对话处理

适用于：处理大量用户查询

```python
import asyncio
import aiohttp

async def batch_chat_processing(queries: list):
    """并发处理多个用户查询"""
    async with aiohttp.ClientSession() as session:
        tasks = [
            process_single_query(session, q)
            for q in queries
        ]
        results = await asyncio.gather(*tasks)
    return results

async def process_single_query(session, query):
    """处理单个查询"""
    # 创建会话
    async with session.post(
        f"{API_URL}/sessions",
        json={"user_id": query['user_id']},
        headers={"Authorization": f"Bearer {TOKEN}"}
    ) as resp:
        session_data = await resp.json()
        session_id = session_data["data"]["id"]
    
    # 发送消息
    response = ""
    async with session.post(
        f"{API_URL}/chat/stream",
        json={"session_id": session_id, "content": query['content']},
        headers={"Authorization": f"Bearer {TOKEN}"}
    ) as resp:
        async for line in resp.content:
            if line.startswith(b"data: "):
                event = json.loads(line[6:])
                if event.get("type") == "content":
                    response += event.get("content", "")
    
    return {"query": query, "response": response}

# 使用
queries = [
    {"user_id": "user_1", "content": "Question 1"},
    {"user_id": "user_2", "content": "Question 2"},
    {"user_id": "user_3", "content": "Question 3"},
]
results = asyncio.run(batch_chat_processing(queries))
```

---

## 错误处理和重试

### 推荐的错误处理模式

```python
import time
from typing import Optional

class RobustLEAPlatformClient(LEAPlatformClient):
    def __init__(self, api_url: str, token: str, max_retries: int = 3):
        super().__init__(api_url, token)
        self.max_retries = max_retries
    
    def _retry_with_backoff(self, func, *args, **kwargs):
        """指数退避重试"""
        for attempt in range(self.max_retries):
            try:
                return func(*args, **kwargs)
            except requests.exceptions.RequestException as e:
                if attempt == self.max_retries - 1:
                    raise
                wait_time = 2 ** attempt
                print(f"Attempt {attempt + 1} failed: {e}. Retrying in {wait_time}s...")
                time.sleep(wait_time)
    
    def create_session_robust(self, user_id: str, title: str = "") -> Optional[dict]:
        """带重试的创建会话"""
        return self._retry_with_backoff(self.create_session, user_id, title)
    
    def send_message_robust(self, session_id: str, content: str) -> str:
        """带错误处理的发送消息"""
        response = ""
        try:
            for chunk in self.send_message(session_id, content):
                response += chunk
        except requests.exceptions.Timeout:
            print("Request timeout - retrying...")
            return self._retry_with_backoff(
                lambda: "".join(self.send_message(session_id, content))
            )
        except requests.exceptions.HTTPError as e:
            print(f"HTTP Error: {e.response.status_code} - {e.response.text}")
            raise
        return response

# 使用
client = RobustLEAPlatformClient(API_URL, TOKEN, max_retries=3)
session = client.create_session_robust("user_123")
response = client.send_message_robust(session["id"], "Hello!")
```

### 常见错误码处理

| 状态码 | 含义 | 处理建议 |
|--------|------|---------|
| `401` | 未授权 | 检查 Token 是否有效或过期 |
| `403` | 禁止访问 | 检查权限或会话归属 |
| `404` | 资源不存在 | 确认 session_id 或 user_id 正确 |
| `429` | 限流 | 等待后重试，实现退避策略 |
| `500` | 服务器错误 | 重试，或联系技术支持 |
| `503` | 服务不可用 | 稍后重试 |

---

## 常见问题

### Q1: 如何在我的网站上集成 LEA Platform？

**A**: 有两种方式：

1. **前端 SDK 方式**（推荐）：
   - 前端直接调用 API
   - 需要处理 CORS 跨域
   - Token 需要安全管理

2. **后端代理方式**（更安全）：
   - 前端调用你的后端
   - 后端代理到 LEA Platform
   - Token 在后端安全保管

```javascript
// 前端调用后端代理
fetch('/api/chat/stream', {
  method: 'POST',
  body: JSON.stringify({ content: userMessage })
})
```

```python
# 后端代理实现
@app.route('/api/chat/stream', methods=['POST'])
def chat_stream():
    user_message = request.json['content']
    response = requests.post(
        f"{LEA_API_URL}/chat/stream",
        json={"session_id": session_id, "content": user_message},
        headers={"Authorization": f"Bearer {LEA_TOKEN}"},
        stream=True
    )
    return response.iter_content()
```

### Q2: Token 泄露了怎么办？

**A**: 立即撤销并重新生成：

1. 登录管理后台
2. 进入 **设置** → **API Keys**
3. 找到泄露的 Token，点击 **撤销**
4. 生成新 Token
5. 更新你的应用配置

### Q3: 支持离线使用吗？

**A**: 不支持。LEA Platform 是云服务，所有请求都需要网络连接和有效的 API Token。

### Q4: 有 SDK 吗？

**A**: 目前没有官方 SDK，但你可以：
- 基于本文档中的示例代码封装
- 使用 Postman Collection 快速测试
- 参考 OpenAPI 规范自动生成代码

### Q5: 如何处理流式响应的超时？

**A**: 建议配置合理的超时时间和重试策略：

```python
requests.post(
    url,
    timeout=60,  # 60 秒超时
    stream=True
)

# 或使用带重试的客户端
client = RobustLEAPlatformClient(API_URL, TOKEN, max_retries=3)
```

---

## 文档资源

- **OpenAPI 规范**: `docs/openapi.yaml` - 完整的 API 规范
- **Postman Collection**: `docs/POSTMAN_COLLECTION.json` - 导入 Postman 立即测试
- **API 文档**: `docs/API_SPECIFICATION.md` - 详细的端点文档
- **错误代码**: 见 API_SPECIFICATION.md 的"错误处理"部分

---

## 技术支持

### 获取帮助

1. **查看文档**: 大多数问题都能在本文档或 API_SPECIFICATION.md 中找到答案
2. **发送邮件**: support@lea-platform.com
3. **开发者论坛**: 访问 https://forum.lea-platform.com
4. **Slack 社区**: 加入 #integrations 频道

### 提交 Bug 报告

提交 Issue 时，请包含：

```markdown
**环境**:
- API Endpoint: xxx
- SDK/Language: Python 3.9
- Token 生成时间: 2026-09-15

**问题描述**:
...

**复现步骤**:
1. ...
2. ...
3. ...

**预期结果**:
...

**实际结果**:
...

**日志/错误**:
```

---

## 更新日志

### v1.0.0 (2026-09-15)

- ✅ 初始版本
- ✅ 完整的集成文档
- ✅ 4 个集成场景示例
- ✅ 错误处理和重试指南

---

**准备好了吗？** 现在就开始集成吧！从"快速开始"部分选择你的编程语言，复制示例代码，然后运行。祝你集成顺利！ 🚀
