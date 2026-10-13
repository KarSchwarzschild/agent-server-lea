# 🐳 Docker 镜像重建 + 前端测试 + 接口文档更新

> **停更提示（2026-10-13）**：本文停更在 2026-09-15，当时还没有 frontend-console，
> 服务与镜像的对应关系也变了（现在 `agent-worker` 共用 `lea-agent:test`、
> `retrieval-worker` 共用 `lea-rag:test`，漏重启会跑旧代码）。
>
> 测试服上的重建步骤以 `docs/TEST_SERVER_DEPLOY.md` 为准。

**日期**: 2026-09-15  
**目的**: 明确哪些服务需要重建、如何做前端测试、接口文档是否要改

---

## 🔴 第 1 部分：Docker 镜像重建

### 1.1 哪些服务需要重建？

| 服务 | 代码变更 | 影响范围 | 需要重建 |
|------|--------|--------|---------|
| **lea-agent** | 32 个 Go 文件 | 核心逻辑（缓存、降级、熔断、Layer 1） | 🔴 **必须** |
| **lea-config** | Python 改动 | 指标集成 + 服务端逻辑 | 🔴 **必须** |
| **lea-rag** | Python 改动 | Celery + 指标集成 | 🔴 **必须** |
| **lea-tools** | Python 改动 + Dockerfile 改 | 工具执行 + 指标集成 | 🔴 **必须** |
| **lea-authkit** | 权限相关改动 | 认证层 | 🟡 可能 |
| **MySQL** | 无代码改动 | 仅需数据迁移 | ✅ 不需要 |
| **Redis** | 无代码改动 | 纯配置 | ✅ 不需要 |
| **Nginx** | 无代码改动 | 纯配置 | ✅ 不需要 |

---

### 1.2 快速重建步骤

#### 方式 A：完整重建（推荐）

```bash
cd D:/gitlab/lea-platform

# 1. 清理旧镜像（可选，如空间不足）
docker rmi lea-agent:dev lea-config:dev lea-rag:dev lea-tools:dev 2>/dev/null || true

# 2. 构建 4 个核心服务
echo "🏗️  Building lea-agent..."
docker build --build-context agentloop=../agentloop \
    -f packages/lea-agent/Dockerfile \
    -t lea-agent:dev .

echo "🏗️  Building lea-config..."
docker build -f packages/lea-config/Dockerfile -t lea-config:dev .

echo "🏗️  Building lea-rag..."
docker build -f packages/lea-rag/Dockerfile -t lea-rag:dev .

echo "🏗️  Building lea-tools..."
docker build -f packages/lea-tools/Dockerfile -t lea-tools:dev .

# 3. 验证镜像构建成功
docker images | grep -E "lea-agent|lea-config|lea-rag|lea-tools" | grep dev

# 4. 启动验证栈
docker-compose -f infra/docker-compose.verify.yml up -d

# 5. 等待服务启动
sleep 60

# 6. 验证所有服务健康
echo "✅ Checking services..."
curl -s http://localhost:8200/health | jq .
curl -s http://localhost:8500/health | jq .
curl -s http://localhost:8300/health | jq .
curl -s http://localhost:8400/health | jq .
```

**预期结果**: 4 个服务都返回 `{"status":"healthy"}` 或类似

---

#### 方式 B：增量重建（开发快速迭代）

```bash
# 只重建改动过的服务
docker build -f packages/lea-agent/Dockerfile -t lea-agent:dev .
docker-compose -f infra/docker-compose.verify.yml restart lea-agent
```

---

#### 方式 C：使用 Docker Compose 自动构建

```bash
# docker-compose.verify.yml 中已配置 build 上下文
# 直接 up 会自动构建镜像
docker-compose -f infra/docker-compose.verify.yml up -d --build

# 强制重建（不使用缓存）
docker-compose -f infra/docker-compose.verify.yml up -d --build --no-cache
```

---

### 1.3 镜像构建验证清单

- [ ] `docker build` 命令执行成功（无 error）
- [ ] 4 个镜像都存在：`docker images | grep dev`
- [ ] 容器启动成功：`docker ps | grep -E "lea-agent|lea-config|lea-rag|lea-tools"`
- [ ] 所有 /health 端点返回 200 OK
- [ ] 所有 /metrics 端点返回 Prometheus 格式数据

---

## 🟢 第 2 部分：前端测试

### 2.1 测试前检查

确保以下条件都满足：

```bash
# 1. Docker 服务都启动了
docker ps | grep -E "lea-agent|nginx|mysql|redis"

# 2. Prometheus + Grafana 也启动了（可选但推荐）
docker ps | grep -E "prometheus|grafana"

# 3. 检查后端 API 是否可访问
curl http://localhost:8080/health      # Nginx 健康检查
curl http://localhost:8200/health      # lea-agent 直连
```

---

### 2.2 新增端点测试（Layer 1 专家切换）

这是本次新增的最关键的功能。前端需要测试以下 3 个新端点：

#### 测试 1：获取当前激活 agent

```bash
curl http://localhost:8100/sessions/{session_id} \
  -H "Authorization: Bearer <token>"

# 响应示例：
# {
#   "id": "sess_xxx",
#   "user_id": "user_123",
#   "active_agent_id": 0,  # ← 新字段
#   "created_at": "2026-09-15T...",
#   ...
# }
```

**前端测试点**:
- [ ] 响应中有 `active_agent_id` 字段
- [ ] 新会话时 `active_agent_id` 默认为 0（默认 superagent）
- [ ] 值类型是数字，不是字符串

---

#### 测试 2：切换专家（PUT 端点）

```bash
# 切换到专家 agent (id=42)
curl -X PUT http://localhost:8100/sessions/{session_id}/active-agent \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"agent_id": 42}'

# 响应示例：
# {
#   "success": true,
#   "applies": "next_message",  # 或 "after_current_turn"
#   "agent_id": 42
# }
```

**前端测试点**:
- [ ] PUT 请求返回 200 OK
- [ ] `applies` 字段告诉前端何时生效（下条消息还是当前轮之后）
- [ ] 错误情况（agent 不存在、无权限）返回 400/403

---

#### 测试 3：取消专家（回到默认）

```bash
# 取消激活 agent（PUT agent_id=0）
curl -X PUT http://localhost:8100/sessions/{session_id}/active-agent \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"agent_id": 0}'

# 响应示例：
# {
#   "success": true,
#   "applies": "next_message",
#   "agent_id": 0
# }
```

**前端测试点**:
- [ ] agent_id=0 时正确理解为"取消激活"
- [ ] 回到默认 superagent 的人格

---

### 2.3 核心链路测试（端到端）

#### 场景 1：新建会话 + 指定专家

```javascript
// 前端代码示例
const response = await fetch('/api/sessions', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    user_id: 'user_123',
    agent_id: 42,  // ← 指定专家
  })
});

const session = await response.json();
console.log('新会话 active_agent_id:', session.active_agent_id);
// 预期: 42
```

**测试要点**:
- [ ] 新会话的 active_agent_id 等于请求中的 agent_id
- [ ] 第一条消息不需要再带 agent_id（已保存到会话）

---

#### 场景 2：中途切换专家

```javascript
// 前端代码示例
// Step 1: 用户在对话中途选择不同的专家
const switchResult = await fetch(`/api/sessions/${sessionId}/active-agent`, {
  method: 'PUT',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ agent_id: 99 })
});

const switchData = await switchResult.json();
console.log('何时生效:', switchData.applies);
// 预期: "next_message" 或 "after_current_turn"

// Step 2: 发送下一条消息
const chatResult = await fetch(`/api/sessions/${sessionId}/chat`, {
  method: 'POST',
  body: JSON.stringify({ content: 'hello' })
});

const chatResponse = await chatResult.json();
console.log('使用的 agent:', chatResponse.agent_id);
// 预期: 99（新的专家）
```

**测试要点**:
- [ ] 切换后的下条消息确实使用新的 agent
- [ ] 历史消息保留（不丢失）
- [ ] 消息内容反映新 agent 的人格

---

#### 场景 3：前端 UI 交互流

```javascript
// 模拟前端 UI：
// 1. 顶部显示当前激活的专家
// 2. 用户点击"切换专家"按钮
// 3. 弹出专家选择菜单
// 4. 用户选择 → 发送 PUT 请求
// 5. 更新 UI（可能显示"下一条消息将使用新专家"的提示）
// 6. 用户继续聊天 → 发送消息请求

const handleSwitchExpert = async (newAgentId) => {
  // 调用 PUT 端点
  const result = await updateActiveAgent(sessionId, newAgentId);
  
  // 根据 applies 更新 UI 提示
  if (result.applies === 'next_message') {
    showNotification('下一条消息将使用新专家');
  } else {
    showNotification('新专家将在本轮对话后生效');
  }
  
  // 刷新会话状态
  await refreshSessionStatus();
};
```

**测试要点**:
- [ ] 前端能正确显示当前激活的专家
- [ ] 切换功能 UI 流畅（按钮、菜单、提示）
- [ ] 错误处理优雅（如无权限时显示错误提示）

---

### 2.4 缓存和降级测试

#### 测试场景：Redis 故障降级

```bash
# 1. 停止 Redis（模拟故障）
docker-compose -f infra/docker-compose.verify.yml pause redis

# 2. 前端发送消息，应该：
curl http://localhost:8100/chat \
  -H "Authorization: Bearer <token>" \
  -d '{"session_id": "...", "content": "hello"}'

# 预期结果：
# - 消息仍能处理（不 brick）
# - 可能延迟增加（因为没缓存，直接回源）
# - 返回正常响应

# 3. 检查日志
docker logs verify-agent | grep -i "redis\|cache" | tail -20

# 4. 恢复 Redis
docker-compose -f infra/docker-compose.verify.yml unpause redis
```

**测试要点**:
- [ ] Redis 故障时聊天仍能继续
- [ ] 无错误返回给用户（fail-soft）
- [ ] 日志记录了降级事件

---

### 2.5 熔断器测试

#### 测试场景：KB 服务故障 → 熔断器打开

```bash
# 1. 停止 lea-rag（模拟 KB 服务故障）
docker-compose -f infra/docker-compose.verify.yml stop lea-rag

# 2. 前端触发需要 KB 的操作
curl http://localhost:8100/chat \
  -H "Authorization: Bearer <token>" \
  -d '{"session_id": "...", "content": "search knowledge", "use_kb": true}'

# 预期结果：
# - 不会 brick（fail-soft 返回备选结果）
# - 告警 CircuitBreakerOpen 应该在 Grafana 中显示

# 3. 查看 Grafana 告警
# 打开 http://localhost:3000
# 选择 "Alerts" → 应该看到 CircuitBreakerOpen

# 4. 恢复 lea-rag
docker-compose -f infra/docker-compose.verify.yml start lea-rag
```

**测试要点**:
- [ ] KB 故障时对话继续（无 KB 结果但不 brick）
- [ ] Grafana 告警正确触发
- [ ] 服务恢复后自动恢复正常

---

## 🔵 第 3 部分：接口文档更新

### 3.1 新增端点汇总

| HTTP 方法 | 端点 | 功能 | 文档状态 |
|-----------|------|------|---------|
| **GET** | `/sessions/{id}` | 获取会话（新增 active_agent_id 字段） | 需更新 |
| **PUT** | `/sessions/{id}/active-agent` | 切换激活 agent | 🔴 **新增** |
| **POST** | `/metrics` | Prometheus 指标收集 | 无需更新（内部） |

### 3.2 需要更新的接口文档文件

检查现有文档：

```bash
find docs -name "*API*" -o -name "*interface*" -o -name "*swagger*"
```

**需要更新的地方**：

1. **`docs/FRONTEND_API.md`**
   - [ ] 新增 `PUT /sessions/:id/active-agent` 端点文档
   - [ ] 更新 `GET /sessions/:id` 响应示例（加 active_agent_id）

2. **`docs/API_CONTRACT.md`**（如有）
   - [ ] 更新会话对象 schema
   - [ ] 新增切换端点 schema

3. **OpenAPI/Swagger 文件**（如有）
   - [ ] 更新 sessions 对象定义
   - [ ] 新增 PUT 端点定义

---

### 3.3 具体更新内容

#### 更新 1：会话对象新增字段

```yaml
# 旧的会话响应
Session:
  type: object
  properties:
    id: string
    user_id: string
    project_id: string
    created_at: string
    updated_at: string

# 新的会话响应（需要添加）
Session:
  type: object
  properties:
    id: string
    user_id: string
    project_id: string
    active_agent_id: integer        # ← 新增字段
    created_at: string
    updated_at: string
```

#### 更新 2：新增切换端点文档

```markdown
## PUT /sessions/{id}/active-agent

切换会话的激活 agent（专家/专家团）。

### 请求

```json
{
  "agent_id": 42  // 0 表示使用默认 superagent
}
```

### 响应（200 OK）

```json
{
  "success": true,
  "applies": "next_message",  // "next_message" 或 "after_current_turn"
  "agent_id": 42
}
```

### 错误响应

- `400 Bad Request`: agent_id 无效
- `403 Forbidden`: 无权限访问该 agent
- `404 Not Found`: 会话不存在
- `409 Conflict`: 会话不属于当前用户

### 说明

- 设置 `agent_id=0` 时回到默认 superagent
- `applies` 字段指示何时生效：
  - `"next_message"`: 下一条用户消息时立即生效
  - `"after_current_turn"`: 当前对话轮次完成后生效
- 前端应根据 `applies` 显示相应的 UI 提示
```

---

### 3.4 检查清单

需要更新接口文档时，请检查：

- [ ] `docs/FRONTEND_API.md` 已更新
- [ ] 会话对象增加 `active_agent_id` 字段说明
- [ ] 新增 `PUT /sessions/:id/active-agent` 的完整文档
- [ ] 包括请求/响应示例和错误处理
- [ ] 前端开发者能理解 `applies` 字段的含义
- [ ] Swagger/OpenAPI 文件（如有）同步更新

---

## 📋 完整执行清单

### 立即执行（今天）

- [ ] **重建 Docker 镜像**
  ```bash
  docker build -f packages/lea-agent/Dockerfile -t lea-agent:dev .
  docker build -f packages/lea-config/Dockerfile -t lea-config:dev .
  docker build -f packages/lea-rag/Dockerfile -t lea-rag:dev .
  docker build -f packages/lea-tools/Dockerfile -t lea-tools:dev .
  docker-compose -f infra/docker-compose.verify.yml up -d
  ```

- [ ] **验证镜像**
  - 4 个服务都返回 /health 200 OK
  - 4 个服务都有 /metrics 端点

### 前端测试（今天或明天）

- [ ] **新端点测试**
  - [ ] PUT /sessions/{id}/active-agent 可调用
  - [ ] 错误情况处理正确

- [ ] **核心链路测试**
  - [ ] 新建会话 + 指定专家
  - [ ] 中途切换专家
  - [ ] 取消激活专家

- [ ] **UI 交互测试**
  - [ ] 专家切换按钮可用
  - [ ] 错误提示正确显示
  - [ ] 提示 "下一条消息生效" 或 "本轮后生效"

- [ ] **降级测试**（可选但推荐）
  - [ ] Redis 故障时聊天继续
  - [ ] KB 故障时聊天继续

### 接口文档更新（本周内）

- [ ] 检查 `docs/FRONTEND_API.md`
- [ ] 更新会话对象 schema
- [ ] 添加 PUT 端点完整文档
- [ ] 同步 Swagger/OpenAPI（如有）
- [ ] 通知前端开发者文档已更新

---

## 🎯 常见问题

### Q1: 镜像构建失败了怎么办？

```bash
# 查看构建日志
docker build -f packages/lea-agent/Dockerfile -t lea-agent:dev . 2>&1 | tail -50

# 清理 dangling 镜像和容器
docker system prune -f

# 重试构建
docker build -f packages/lea-agent/Dockerfile --no-cache -t lea-agent:dev .
```

### Q2: 前端测试时 API 返回 401 怎么办？

确保：
- 携带了正确的 `Authorization: Bearer <token>` 头
- token 未过期
- 用户有权限访问该会话

### Q3: 接口文档在哪里？

检查以下位置：
```bash
ls -la docs/*API* docs/*interface* docs/*swagger*
```

如果没有找到，新建文件：
```bash
touch docs/FRONTEND_API.md
```

---

## 📞 技术支持

- **镜像构建问题** → 检查 Dockerfile 和依赖
- **前端集成问题** → 查看 DEPLOYMENT_GUIDE.md 的"前端接口"部分
- **文档更新问题** → 参考 docs/FRONTEND_API.md 的现有格式

---

**完成上述步骤后，你就有了：**
1. ✅ 最新的 Docker 镜像（含所有代码变更）
2. ✅ 经过前端验证的新功能
3. ✅ 最新的接口文档（供前端开发者参考）

**现在就开始吧！** 🚀
