# 🚀 LEA Platform API 文档包

欢迎使用 LEA Platform！本目录包含了完整的 API 文档，供第三方开发者快速集成。

---

## 📖 文档快速导航

### 🎯 我是第一次来，该从哪里开始？

**→ [THIRD_PARTY_INTEGRATION_GUIDE.md](./THIRD_PARTY_INTEGRATION_GUIDE.md)**

这是一份友好的入门指南，包含：
- ✅ 最简单的代码示例（Python + JavaScript）
- ✅ 4 个真实的集成场景
- ✅ 常见问题解答
- ✅ 错误处理最佳实践

**预计阅读时间**: 15-20 分钟

---

### ⚡ 我需要快速查询某个 API 端点

**→ [API_QUICK_REFERENCE.md](./API_QUICK_REFERENCE.md)**

一份简洁的速查表，包含：
- ✅ 所有端点的快速参考（表格形式）
- ✅ 一行命令快速测试
- ✅ 常见工作流
- ✅ cURL 示例

**预计查询时间**: 5 分钟

---

### 📚 我需要完整的 API 文档

**→ [API_SPECIFICATION.md](./API_SPECIFICATION.md)**

详细的端点文档，包含：
- ✅ 所有 24 个 REST 端点的完整说明
- ✅ 每个端点的请求/响应示例
- ✅ 参数和返回值的详细说明
- ✅ 错误码和处理方式

**预计阅读时间**: 30-40 分钟

---

### 📮 我想在 Postman 中测试

**→ [POSTMAN_COLLECTION.json](./POSTMAN_COLLECTION.json)**

直接在 Postman 中导入和测试，包含：
- ✅ 所有端点的预配置请求
- ✅ 示例请求体
- ✅ 环境变量（base_url, token）
- ✅ 即开即用

**导入步骤**:
1. 打开 Postman
2. 点击 **Import** → 选择文件 → 上传 `POSTMAN_COLLECTION.json`
3. 在环境中配置 `base_url` 和 `token`
4. 开始测试！

---

### 🔧 我需要 OpenAPI 规范

**→ [openapi.yaml](./openapi.yaml)**

机器可读的 API 规范，用于：
- ✅ 自动生成 SDK（TypeScript, Python, Go 等）
- ✅ 集成到其他开发工具
- ✅ 生成 Swagger UI 文档
- ✅ API 文档自动化

---

### 📚 我想了解所有文档的结构

**→ [API_DOCUMENTATION_INDEX.md](./API_DOCUMENTATION_INDEX.md)**

完整的文档导航和索引，包含：
- ✅ 所有文档的详细说明
- ✅ 按场景选择文档的指南
- ✅ 文档对应关系图
- ✅ 工具推荐

---

## 🎯 按场景快速开始

### 场景 1: 快速集成（30 分钟）

```
1. 阅读: THIRD_PARTY_INTEGRATION_GUIDE.md（快速开始部分）
2. 复制: Python 或 JavaScript 示例代码
3. 修改: 替换 token 和 API_URL
4. 运行: 测试代码
5. 完成: ✅
```

### 场景 2: 生产级集成（2-3 小时）

```
1. 阅读: THIRD_PARTY_INTEGRATION_GUIDE.md（全文）
2. 学习: API_SPECIFICATION.md（特别是错误处理部分）
3. 参考: API_QUICK_REFERENCE.md（工作流和最佳实践）
4. 实现: 自己的客户端库
5. 测试: 在测试环境中充分验证
6. 生产: 部署到生产环境
```

### 场景 3: 在 Postman 中测试（5 分钟）

```
1. 导入: POSTMAN_COLLECTION.json 到 Postman
2. 配置: 设置 base_url 和 token
3. 测试: 点击任何请求，按 Send
4. 查看: Response 和状态码
5. 完成: ✅
```

### 场景 4: 自动生成 SDK（10 分钟）

```
1. 下载: openapi.yaml
2. 使用: OpenAPI Generator（https://openapi-generator.tech/）
3. 生成: 选择你的语言（Python/JS/Go/等）
4. 使用: 在你的项目中导入生成的 SDK
5. 完成: ✅
```

---

## 🔐 获取 API Token

### 方式 1: 从管理后台生成（推荐）

1. 登录 LEA Platform 管理后台
2. 导航到 **设置** → **API Keys**
3. 点击 **生成新 Token**
4. 复制 Token 到你的配置中

```bash
export LEA_API_TOKEN="your_token_here"
```

### 方式 2: 联系管理员

向 LEA Platform 管理员申请 API Token，提供你的组织和应用名称。

---

## 🚀 最简单的示例（3 行代码）

### Python

```python
from THIRD_PARTY_INTEGRATION_GUIDE import LEAPlatformClient

client = LEAPlatformClient("http://localhost:8100/api", "your_token")
session = client.create_session("user_123")
for chunk in client.send_message(session["id"], "Hello!"):
    print(chunk, end="")
```

### JavaScript

```javascript
import { LEAPlatformClient } from './THIRD_PARTY_INTEGRATION_GUIDE.js';

const client = new LEAPlatformClient("http://localhost:8100/api", "your_token");
const session = await client.createSession("user_123");
for await (const chunk of client.sendMessage(session.id, "Hello!")) {
    process.stdout.write(chunk);
}
```

### cURL

```bash
# 创建会话
curl -X POST http://localhost:8100/api/sessions \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"user_123"}'

# 发送消息（流式）
curl -X POST http://localhost:8100/api/chat/stream \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"sess_abc123","content":"Hello"}' \
  -N
```

---

## 📋 核心概念

### API 基础 URL

```
开发环境: http://localhost:8100/api
生产环境: https://api.lea-platform.com/api
```

### 认证方式

所有请求都需要在 Header 中包含 Bearer Token：

```bash
Authorization: Bearer YOUR_TOKEN_HERE
```

### 请求和响应格式

- **Content-Type**: `application/json`
- **编码**: UTF-8
- **响应格式**: 统一的 JSON 结构

### 限流

- **限额**: 100 请求 / 分钟 / 用户
- **超出时**: 返回 429 状态码
- **建议**: 实现指数退避重试

---

## ✅ 检查清单

在开始集成前，请确认：

- [ ] 已获得有效的 API Token
- [ ] 已确定你的集成场景（简单聊天/多轮对话/专家切换）
- [ ] 已选择编程语言（Python/JavaScript/其他）
- [ ] 已读过对应语言的示例代码
- [ ] 已在开发环境中测试基本功能
- [ ] 已实现错误处理和重试机制
- [ ] 已在测试环境中充分验证

---

## 🆘 遇到问题？

### 常见问题

- **"401 Unauthorized"** → 检查 token 是否有效
- **"404 Not Found"** → 检查 session_id 是否正确
- **"429 Too Many Requests"** → 实现重试机制，等待后重试
- **"500 Internal Server Error"** → 服务器错误，稍后重试

### 获取帮助

- 📖 **查看文档**: 优先查看 API_QUICK_REFERENCE.md 或 API_SPECIFICATION.md
- 📧 **发送邮件**: support@lea-platform.com
- 💬 **Slack 社区**: #integrations 频道
- 🐛 **提交 Issue**: 包含详细的错误信息和复现步骤

---

## 📚 文档清单

| 文档 | 用途 | 适合人群 |
|------|------|---------|
| [THIRD_PARTY_INTEGRATION_GUIDE.md](./THIRD_PARTY_INTEGRATION_GUIDE.md) | 📖 完整的集成指南 | 第一次集成的开发者 |
| [API_SPECIFICATION.md](./API_SPECIFICATION.md) | 📚 详细的 API 文档 | 需要完整参考的开发者 |
| [API_QUICK_REFERENCE.md](./API_QUICK_REFERENCE.md) | ⚡ 快速参考表 | 查询特定端点的开发者 |
| [POSTMAN_COLLECTION.json](./POSTMAN_COLLECTION.json) | 📮 Postman 集合 | 想快速测试的开发者 |
| [openapi.yaml](./openapi.yaml) | 🔧 OpenAPI 规范 | 需要生成代码的开发者 |
| [API_DOCUMENTATION_INDEX.md](./API_DOCUMENTATION_INDEX.md) | 📚 文档导航 | 想了解全貌的开发者 |

---

## 🌟 推荐的集成路径

```
新手路径（推荐）:
  1. 阅读本文档（5 min）
  2. 选择场景，按照上面的"按场景快速开始"操作
  3. 查看对应文档中的代码示例
  4. 在你的项目中运行示例代码
  5. 逐步扩展功能

专家路径:
  1. 浏览 API_SPECIFICATION.md 的端点列表
  2. 使用 openapi.yaml 生成 SDK
  3. 在你的项目中使用 SDK
  4. 参考 THIRD_PARTY_INTEGRATION_GUIDE.md 处理错误
  5. 部署到生产
```

---

## 📞 技术支持

### 文档问题

如果你发现文档中有错误或不清楚的地方，请：
1. 查看 API_DOCUMENTATION_INDEX.md 中是否有更详细的解释
2. 发邮件到 support@lea-platform.com
3. 在 GitHub 提交 Issue

### API 问题

如果 API 调用失败：
1. 检查 token 是否有效
2. 查看 API_QUICK_REFERENCE.md 中的错误码解释
3. 参考 THIRD_PARTY_INTEGRATION_GUIDE.md 中的错误处理方案
4. 如果问题仍未解决，联系技术支持

---

## 📊 文档信息

```
版本: 1.0.0
生成日期: 2026-09-15
总文档数: 6 份
总大小: ~70 KB
代码示例: 20+ 个
端点覆盖: 24 个 REST 端点
```

---

## 🎉 准备好了吗？

选择适合你的文档开始吧！

- **第一次？** → [THIRD_PARTY_INTEGRATION_GUIDE.md](./THIRD_PARTY_INTEGRATION_GUIDE.md)
- **快速查询？** → [API_QUICK_REFERENCE.md](./API_QUICK_REFERENCE.md)
- **完整文档？** → [API_SPECIFICATION.md](./API_SPECIFICATION.md)
- **Postman 测试？** → [POSTMAN_COLLECTION.json](./POSTMAN_COLLECTION.json)
- **代码生成？** → [openapi.yaml](./openapi.yaml)
- **想了解全貌？** → [API_DOCUMENTATION_INDEX.md](./API_DOCUMENTATION_INDEX.md)

**祝你集成顺利！** 🚀

---

**最后更新**: 2026-09-15  
**维护方**: LEA Platform 团队
