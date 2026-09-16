# 📚 LEA Platform API 文档完整索引

**生成日期**: 2026-09-15  
**版本**: 1.0.0  
**适用对象**: 第三方开发者 / 集成方

---

## 🎯 快速导航

### 我是**第一次接触**，应该看什么？

👉 **[THIRD_PARTY_INTEGRATION_GUIDE.md](./THIRD_PARTY_INTEGRATION_GUIDE.md)** (10 min)

从"快速开始"开始，包含最简单的代码示例（Python + JavaScript）。

---

### 我需要**具体的端点文档**

👉 **[API_SPECIFICATION.md](./API_SPECIFICATION.md)** (20 min)

完整的端点列表，包括：
- 所有请求/响应示例
- 参数说明
- 错误码处理
- 最佳实践

---

### 我想**快速查询**某个端点

👉 **[API_QUICK_REFERENCE.md](./API_QUICK_REFERENCE.md)** (5 min)

表格形式的快速参考：
- 核心端点速查表
- 一行命令快速测试
- 常见工作流
- 常见代码片段

---

### 我要**在 Postman 中测试**

👉 **[POSTMAN_COLLECTION.json](./POSTMAN_COLLECTION.json)** (即时使用)

导入方式：
1. 打开 Postman
2. 点击 Import → 选择文件 → 上传 JSON
3. 配置 `{{base_url}}` 和 `{{token}}` 变量
4. 立即测试所有端点

---

### 我需要 **OpenAPI 规范**（给代码生成工具）

👉 **[openapi.yaml](./openapi.yaml)** (机器可读)

用于：
- Swagger UI 渲染
- 自动生成 SDK（TypeScript, Python, Go 等）
- API 文档自动化
- 与其他工具集成

---

## 📂 文档完整清单

| 文档 | 格式 | 大小 | 用途 | 读者 |
|------|------|------|------|------|
| **API_SPECIFICATION.md** | Markdown | ~15 KB | 📖 详细 API 文档 | 开发者 |
| **API_QUICK_REFERENCE.md** | Markdown | ~8 KB | ⚡ 速查表 | 开发者 |
| **openapi.yaml** | YAML | ~12 KB | 🔧 机器可读规范 | 工具 / IDE |
| **POSTMAN_COLLECTION.json** | JSON | ~8 KB | 📮 Postman 测试集合 | QA / 开发者 |
| **THIRD_PARTY_INTEGRATION_GUIDE.md** | Markdown | ~20 KB | 🚀 集成指南 + 代码示例 | 第三方开发者 |
| **API_DOCUMENTATION_INDEX.md** | Markdown | ~6 KB | 📚 本文档 | 所有人 |

---

## 🔄 集成流程全景图

```
第三方开发者
    ↓
1️⃣  阅读 THIRD_PARTY_INTEGRATION_GUIDE.md
    ↓ (了解基本概念)
    ├─→ 选择编程语言 (Python / JavaScript / Go)
    ├─→ 复制示例代码到本地
    ├─→ 替换 token 和 API_URL
    └─→ 运行测试
    ↓
2️⃣  需要具体端点信息？
    ├─→ 查看 API_SPECIFICATION.md
    └─→ 或 API_QUICK_REFERENCE.md
    ↓
3️⃣  想在 Postman 中测试？
    ├─→ 导入 POSTMAN_COLLECTION.json
    ├─→ 配置变量
    └─→ 点击发送
    ↓
4️⃣  需要自动生成代码？
    ├─→ 使用 openapi.yaml
    ├─→ 用 OpenAPI 生成器生成 SDK
    └─→ 在你的项目中使用 SDK
    ↓
✅ 完成集成！
```

---

## 📑 按场景查文档

### 场景 1: "我想快速开始"

```
文档阅读顺序：
1. THIRD_PARTY_INTEGRATION_GUIDE.md (第一部分 "快速开始")
2. API_QUICK_REFERENCE.md (看 "一行命令快速测试")
3. 选择示例代码，修改 token 和 session_id，运行
```

**预期花时**: 15 分钟

---

### 场景 2: "我要集成到生产"

```
文档阅读顺序：
1. THIRD_PARTY_INTEGRATION_GUIDE.md (全文，特别是"错误处理和重试"部分)
2. API_SPECIFICATION.md (了解所有端点，特别是错误码)
3. API_QUICK_REFERENCE.md (最佳实践)
4. 自己的代码审查（特别是 Token 管理和错误处理）
```

**预期花时**: 2-3 小时

---

### 场景 3: "我要用 Postman 快速测试"

```
步骤：
1. 打开 Postman
2. Import → 选择 POSTMAN_COLLECTION.json
3. 设置 Environment 变量：
   - base_url: http://localhost:8100/api
   - token: your_token_here
4. 在集合中选择请求，点击 Send
5. 查看 Response 和 Status Code
```

**预期花时**: 5 分钟

---

### 场景 4: "我想自动生成 SDK"

```
使用 OpenAPI 生成器（免费工具）：

JavaScript/TypeScript:
  npx @openapitools/openapi-generator-cli generate \
    -i openapi.yaml \
    -g typescript \
    -o ./generated

Python:
  openapi-generator-cli generate \
    -i openapi.yaml \
    -g python \
    -o ./generated

Go:
  openapi-generator-cli generate \
    -i openapi.yaml \
    -g go \
    -o ./generated

然后在你的项目中使用生成的 SDK。
```

**预期花时**: 10 分钟

---

### 场景 5: "我要处理复杂的集成场景"

```
文档阅读顺序：
1. THIRD_PARTY_INTEGRATION_GUIDE.md (集成场景 2-4)
2. API_SPECIFICATION.md (了解所有字段和错误)
3. 根据需要参考 API_QUICK_REFERENCE.md (工作流)
4. 自己实现对应的客户端库
```

**预期花时**: 3-5 小时

---

## 📋 关键概念速查

### 1. Bearer Token 认证

所有请求都需要在 Header 中包含 token：

```bash
Authorization: Bearer YOUR_TOKEN_HERE
```

详见: API_QUICK_REFERENCE.md → "认证" 部分

---

### 2. 流式 API（SSE）

最推荐的方式，提供实时响应：

```
POST /chat/stream
Response: Server-Sent Events (SSE)
```

详见: API_SPECIFICATION.md → "发送消息（流式响应）" 部分

---

### 3. 会话管理

每个用户的对话都在一个"会话"中进行：

```
1. 创建会话: POST /sessions
2. 发送消息: POST /chat/stream
3. 获取历史: GET /sessions/{id}/messages
4. 删除会话: DELETE /sessions/{id}
```

详见: API_QUICK_REFERENCE.md → "工作流 1" 部分

---

### 4. 专家切换（Layer 1）

可以动态切换 AI 的人格和能力：

```
1. 创建会话时指定: POST /sessions (agent_id=42)
2. 或中途切换: PUT /sessions/{id}/active-agent
3. agent_id=0 表示回到默认
```

详见: THIRD_PARTY_INTEGRATION_GUIDE.md → "场景 3：使用专家"

---

### 5. 错误处理和重试

一定要实现重试逻辑，特别是对于：
- 429 (限流)
- 5xx (临时错误)

详见: THIRD_PARTY_INTEGRATION_GUIDE.md → "错误处理和重试"

---

## 🛠️ 工具推荐

### 测试 API

- **Postman**: 导入 POSTMAN_COLLECTION.json 即可使用
- **cURL**: API_QUICK_REFERENCE.md 有一行命令示例
- **Insomnia**: 也支持 OpenAPI 规范导入
- **VS Code**: 安装 REST Client 扩展，直接编写 .http 文件测试

### 代码生成

- **OpenAPI Generator**: 从 openapi.yaml 生成各种语言的 SDK
- **Swagger UI**: 可视化 API 文档（用 openapi.yaml）
- **ReDoc**: 更现代的 API 文档展示（用 openapi.yaml）

### 监控和日志

- **Postman Monitor**: 定期测试 API（付费功能）
- **Insomnia Inso CLI**: CI/CD 集成
- **OpenAPI Spec 验证**: spectacle 或 ibm/openapi-validator

---

## 🔐 安全建议

### Token 管理

- ✅ 存储在环境变量（`.env`）
- ✅ 使用密钥管理系统（如 AWS Secrets Manager）
- ✅ 定期轮换（建议 90 天）
- ✅ 只授予必需的权限
- ❌ 不要在代码中硬编码
- ❌ 不要提交到版本控制
- ❌ 不要在日志中打印

### API 使用

- ✅ 使用 HTTPS 在生产环境
- ✅ 实现请求超时
- ✅ 实现重试机制
- ✅ 记录 API 调用日志（用于调试）
- ❌ 不要暴露内部错误详情给用户

---

## 📊 文档对应关系图

```
一般开发者的学习路径：
├─ THIRD_PARTY_INTEGRATION_GUIDE.md (入门)
│  ├─ 快速开始
│  ├─ 获取 Token
│  ├─ Python 示例
│  ├─ JavaScript 示例
│  └─ 4 个集成场景
│
├─ API_SPECIFICATION.md (深入)
│  ├─ 会话管理 (6 个端点)
│  ├─ 聊天接口 (8 个端点)
│  ├─ 项目管理 (5 个端点)
│  ├─ 文件管理 (5 个端点)
│  └─ 错误处理
│
├─ API_QUICK_REFERENCE.md (查询)
│  ├─ 端点速查表
│  ├─ SSE 事件类型
│  ├─ 3 个工作流
│  ├─ 常见代码片段
│  └─ cURL 示例
│
├─ POSTMAN_COLLECTION.json (测试)
│  ├─ 直接导入 Postman
│  ├─ 所有端点都有示例
│  ├─ 内置变量
│  └─ 可立即运行
│
└─ openapi.yaml (集成)
   ├─ 机器可读规范
   ├─ 支持代码生成
   ├─ Swagger UI 渲染
   └─ 与其他工具兼容
```

---

## 🚀 开始集成的 3 步

### Step 1: 获取 Token

登录管理后台 → 设置 → API Keys → 生成新 Token

### Step 2: 选择你的方式

- **快速测试?** → 用 Postman 导入 POSTMAN_COLLECTION.json
- **写代码?** → 复制 THIRD_PARTY_INTEGRATION_GUIDE.md 的示例代码
- **查端点?** → 打开 API_QUICK_REFERENCE.md 速查

### Step 3: 运行和调试

- 替换 token 和 API_URL
- 参考错误处理建议处理异常
- 在生产前充分测试

---

## 📞 获取帮助

### 文档中有答案吗？

1. API_QUICK_REFERENCE.md (最快)
2. API_SPECIFICATION.md (最详细)
3. THIRD_PARTY_INTEGRATION_GUIDE.md (最友好)

### 还是没解决？

- 📧 **Email**: support@lea-platform.com
- 💬 **Slack**: #integrations 频道
- 🐛 **Issue**: 提交到 GitHub（包含详细信息）

---

## 📝 更新日志

### v1.0.0 (2026-09-15)

- ✅ 完整的 API 文档 (Markdown + OpenAPI)
- ✅ Postman Collection（包含所有端点）
- ✅ 第三方集成指南（含代码示例）
- ✅ 快速参考文档（端点速查表）
- ✅ 本文档（导航索引）

---

## 📊 文件统计

```
总文档数: 6 份
总大小: ~70 KB
格式: Markdown (4) + YAML (1) + JSON (1)
代码示例: 20+ 个（Python, JavaScript, cURL）
端点覆盖: 24 个 REST 端点
错误码: 8 种主要状态码
```

---

## ✅ 检查清单

在开始集成前，确认：

- [ ] 已获得有效的 API Token
- [ ] 知道自己要集成哪个场景（简单聊天 / 多轮对话 / 专家切换 / 工具挂载）
- [ ] 已选择编程语言
- [ ] 已浏览对应的文档
- [ ] 已测试基本的 API 调用（创建会话 + 发送消息）
- [ ] 已实现错误处理和重试机制
- [ ] 已在测试环境中充分验证

---

**版本**: 1.0.0  
**生成日期**: 2026-09-15  
**维护方**: LEA Platform 团队

**准备好了吗？** 从上面选择最适合你的文档，开始集成吧！🚀
