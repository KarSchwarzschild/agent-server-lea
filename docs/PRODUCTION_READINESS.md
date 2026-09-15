# 生产就绪评估

> 当前代码是否能支持高并发多节点生产环境？诚实的答案。

---

## 总体结论

**部分就绪。** 核心链路可以支持多节点，但有几个关键缺口。

| 维度 | 现状 | 生产风险 | 优先级 |
|------|------|---------|--------|
| 数据库连接池 | ✅ 配置了 | 低 | - |
| 缓存层 | ⚠️ 不统一 | 中 | NOW |
| 跨节点协调 | ✅ Redis 架构 | 低 | - |
| 锁与竞态 | ✅ Redis 分布式锁 | 低 | - |
| 消息队列 | ❌ 没有 | 高 | NOW |
| 异步任务 | ❌ 没有系统 | 高 | NOW |
| 会话淘汰 | ✅ 有 TTL | 低 | - |
| 优雅关闭 | ✅ 实现了 | 低 | - |

---

## 详细分析

### 1. 数据库连接池 ✅

**现状**：lea-agent 配置了
```go
db.SetMaxOpenConns(20)
db.SetMaxIdleConns(5)
db.SetConnMaxLifetime(30 * time.Minute)
```

**评估**：
- 20 个连接在 5-10 节点集群里是够的（假设平均负载下每个 pod 不超过 2-3 个并发查询）
- 30 分钟连接生命周期可以应付数据库端的超时重置
- lea-config/lea-rag 用 SQLAlchemy 的 Pool，默认 pool_size=5，pool_pre_ping=true，也可以

**缺陷**：
- 没看到 lea-tools 显式配置（默认用 SQLAlchemy 的 QueuePool，大概可以）
- 没有连接池监控（无法观测满水位或泄漏）

**建议**：
- 加监控：定期日志输出当前活跃连接数
- 如果高峰期经常看到 20 的 max，要往上调（改成 50）

---

### 2. 缓存层 ⚠️

**现状**：
- lea-agent：**没有缓存层**（每轮查询走数据库）
- lea-tools：有 Redis 缓存（`get_cached_capabilities`）
- lea-config：没看到

**高并发问题**：
- 某个 agent 配置被 1000 个前端同时请求 → 1000 个数据库查询
- 某个知识库的文档列表被频繁访问 → 重复扫描

**建议** (NOW)：

1. **lea-agent 的 agent 配置缓存**
   ```go
   // 在 Manager 里加一层 agent 配置缓存（TTL=5min）
   agentConfigCache := make(map[int]AgentConfig)
   configCacheTTL := 5 * time.Minute
   ```
   
2. **lea-config 的知识库列表缓存**
   ```python
   @lru_cache(maxsize=1000)
   def get_kbs_for_agent(agent_id: int, tenant_id: str):
       # 每个 (agent_id, tenant_id) 缓存 5 分钟
   ```

3. **lea-tools 的工具列表缓存** — 已经有了，保持

**缓存一致性**：
- 当用户修改 agent 配置时，清掉缓存（简单方案，可以先用）
- 或者用 Redis 的发布/订阅让所有节点同时失效

---

### 3. 跨节点协调 ✅

**现状**：
- lea-agent 用 Redis + 分布式锁（`internal/lock/locker.go`）
- 控制面（control plane）也用 Redis pub/sub
- SSE 重连用 Redis Stream（如果配了 REDIS_URL）

**代码证据**：
```go
// manager.go
locker *lock.Locker  // Redis 分布式锁
control control.Controller  // Redis 发布/订阅

// 多节点场景：
// 1. pod-A 拿到锁 → 独占这个 session 的 turn
// 2. pod-B 请求同一个 session → 拿不到锁，返 409 Conflict
// 3. pod-C 想中断 pod-A 的生成 → 发 control 消息
```

**缺陷**：
- Redis pub/sub **没有投递保证**（见代码注释）
  - 目标 pod 如果断开重连期间，消息丢失
  - 但这个场景很少见（通常用户不会频繁按"停止"）
  - 改成 Redis Stream 需要额外工作

**评估**：生产可用，但不是"至少一次"语义

**建议** (BETA)：
- 换成 Redis Stream 的消费者组模式（保证至少一次）
- 或者简单方案：中断消息放在数据库的 signals 表，轮询读

---

### 4. 锁与竞态 ✅

**现状**：
- Agent session 有 sync.RWMutex（单节点内的锁）
- 跨节点用 Redis Locker（分布式锁）
- turn 执行靠 locker 保证只有一个 pod 在跑

**代码**：
```go
// agent_session.go
mu          sync.RWMutex       // 会话对象本身的锁
collectorMu sync.Mutex         // 收集器的锁
turnMu      sync.Mutex         // 轮次的锁
locker      *lock.Locker       // 跨 pod 的执行权

// 多节点场景：
// 1. 本地读操作加 RWMutex.RLock()
// 2. 写操作加 RWMutex.Lock()
// 3. turn 执行前加 locker.Acquire()
```

**评估**：设计合理，应该没有数据竞态

**缺陷**：
- 没看到死锁检测（假如有 A→B→A 的锁顺序问题）
- 没有锁超时（假如一个 pod crash，持有的锁永远释放不了）

**建议** (BETA)：
- Redis locker 要设置 TTL（自动失效，防止孤儿锁）
- 检查代码里有没有嵌套加锁的地方

---

### 5. 消息队列 ❌

**现状**：**没有消息队列系统**

**问题场景**：

1. **用户上传知识库文档** → lea-rag 解析
   - 现在：同步处理（上传端阻塞直到解析完）
   - 风险：大文件解析 10 分钟 → 连接超时 → 用户以为失败 → 重复上传

2. **清理过期会话、沙箱** → 后台任务
   - 现在：在 Manager.sweep() 里轮询清理
   - 风险：如果清理逻辑卡住，会 block 主 goroutine

3. **生成报表、日志归档** — 异步批处理
   - 现在：没地方放

**建议** (NOW)：

用 Redis 做一个简单的任务队列（不需要 Celery 或 Kafka）：

```python
# lea-rag 接收上传
@app.post("/kb/{kb_id}/upload")
async def upload_document(file: UploadFile):
    # 1. 保存文件到 S3/NFS
    # 2. 入队一个解析任务
    task = {
        "type": "parse_document",
        "kb_id": kb_id,
        "doc_id": doc_id,
        "file_key": file_key
    }
    redis_client.rpush("lea:tasks", json.dumps(task))
    return {"doc_id": doc_id, "status": "queued"}

# 后台 worker（可以是同一个 pod 的另一条 goroutine）
while True:
    task = redis_client.blpop("lea:tasks", timeout=1)
    if not task:
        continue
    try:
        process_task(task)
    except Exception as e:
        # 重新入队或死信队列
        redis_client.rpush("lea:dead-letter", json.dumps(task))
```

**对比不同方案**：

| 方案 | 复杂度 | 可靠性 | 何时用 |
|------|--------|--------|--------|
| Redis List | 低 | 中（无持久化）| NOW — 快速启动 |
| Redis Stream | 低 | 中 | 需要消费者组时 |
| RabbitMQ | 中 | 高 | 3 个月后，业务量大了 |
| Kafka | 高 | 最高 | 数百万消息/秒 |

**推荐**：先用 Redis List 的 FIFO queue，简单够用。

---

### 6. 异步任务系统 ❌

**现状**：**没有统一的异步任务框架**

**问题**：
- 沙箱执行用 asyncio（Python）
- Go 侧用 goroutine
- 没有失败重试、死信队列、幂等性保证

**建议** (NOW)：

1. **定义任务类型**
   ```json
   {
     "type": "parse_document|generate_report|cleanup_sandbox",
     "id": "task-uuid",
     "params": {},
     "retry": 3,
     "deadline": "2026-09-14T10:05:00Z",
     "created_at": "2026-09-14T10:00:00Z"
   }
   ```

2. **入队和处理分离**
   ```go
   // Go: 入队任务
   enqueueTask("parse_document", map[string]any{
       "kb_id": kb_id,
       "doc_id": doc_id,
   })
   
   // Python: Worker 处理
   task = dequeue_task()
   try:
       process_task(task)
       mark_done(task.id)
   except Exception as e:
       if task.retry > 0:
           re_enqueue(task)
       else:
           mark_failed(task.id)
   ```

3. **幂等性**
   - 任务执行前检查"这个 ID 有没有跑过"
   - 防止重试导致重复处理

---

### 7. 会话淘汰 ✅

**现状**：
```go
// manager.go
const defaultSessionIdleTTL = 30 * time.Minute
const defaultMaxSessions = 500

// sweep 函数定期清理空闲会话
func (m *Manager) sweep() {
    // 按 TTL 清理，容量超了也清理
}
```

**评估**：
- 30 分钟 TTL 合理（不会积压）
- 500 个会话上限合理（每个 pod）
- 自适应扫描周期好

**缺陷**：
- 没有观测：不知道平时有多少活跃会话、清理删了多少

**建议**：
- 加日志：`log.Printf("swept %d sessions", count)`
- 加指标上报

---

### 8. 优雅关闭 ✅

**现状**：
```go
// main.go
const defaultDrainTimeout = 3 * time.Minute
const defaultPreStopDelay = 5 * time.Second

// 收到 SIGTERM 后：
// 1. 翻掉健康检查（preStop）
// 2. 等 5 秒让 kube-proxy 摘掉 endpoint
// 3. 停止接收新请求
// 4. 等正在跑的 turn 完成（最多 3 分钟）
// 5. 关闭 Redis / DB 连接
```

**评估**：非常好。完全避免了"杀掉正在处理的请求"的问题

**建议**：确保 k8s 的 `terminationGracePeriodSeconds > 200`（大于 drain timeout）

---

## 缺口清单与修复顺序

### 立即做（上线前 1 周）

1. **加缓存层**
   - lea-agent: agent 配置缓存（TTL 5min）
   - lea-config: KB/agent/skill 列表缓存
   - 成本：2-3 工程日
   - 风险：低（缓存失效就回源）

2. **加消息队列**
   - 用 Redis List 实现简单的 FIFO
   - 支持文档解析、清理等异步任务
   - 成本：2-3 工程日
   - 风险：中（要测失败重试场景）

3. **加任务框架**
   - 定义统一的任务格式
   - Worker 模式（失败重试、幂等性）
   - 成本：1-2 工程日
   - 风险：低

### Beta 阶段（上线 1 个月内）

4. **监控和告警**
   - 连接池使用率
   - 队列深度
   - 错误率
   - 成本：2-3 工程日

5. **控制面消息可靠性**
   - 改成 Redis Stream
   - 消费者组 + ACK
   - 成本：1-2 工程日

### 生产稳定期（3 个月后）

6. **换成生产级消息队列**
   - Redis → RabbitMQ 或 Kafka
   - 成本：3-5 工程日

---

## 并发能力预估

假设：
- 20 个数据库连接 × 5 个 pod = 100 个连接
- 远程 MySQL 单机（202.205.160.9:3307）

**当前瓶颈**：

| 场景 | QPS | 持续时间 | 会碰到问题吗 |
|------|-----|---------|-------------|
| 100 个并发用户，每人 1 req/s | 100 | 可持续 | ✅ 不会 |
| 1000 个并发用户，每人 0.1 req/s | 100 | 可持续 | ✅ 不会 |
| 10 个超级用户，每人 50 req/s（API 轮询）| 500 | 10s | ⚠️ 可能 |
| 100 个用户同时上传大文档解析 | 100 个任务堆积 | 5min | ❌ 会卡 |

**现在的痛点是同步处理长任务** — 解析文档、生成报告。用消息队列改成异步就解决了。

---

## 建议的发布清单

```
□ 缓存层实现并测试
□ 消息队列 Redis List 实现并测试
□ 异步任务框架（失败重试、幂等性）
□ 监控和日志（连接池、队列深度）
□ 多节点集成测试（3 个 pod，互相中断）
□ 压力测试（1000 并发、持续 10 分钟）
□ 故障场景（pod crash、DB 抖动、Redis 断开）
□ 文档：运维手册、扩容指南、告警规则
```

---

## 总结

**现在可以上生产吗？** 功能上可以，但可靠性还差一个档次。

**关键缺口** — 都可以快速补上：
1. 缓存（防止 DB 过载）
2. 异步任务队列（防止长任务卡死）
3. 监控（及早发现问题）

**3 天内能补上吗？** 可以。优先级：缓存 > 队列 > 监控。

**不补会怎样？** 大部分正常，但高峰期会看到：
- 响应慢（DB 连接池满）
- 任务超时（文档解析 10 分钟但请求 5 分钟超时）
- 看不到问题在哪里（无监控）
