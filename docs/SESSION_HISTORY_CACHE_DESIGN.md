# 📋 会话历史缓存架构设计 - Redis + MySQL 分层方案

**日期**: 2026-09-15  
**问题**: 会话历史和上下文的存储和缓存策略

---

## 🎯 当前状态分析

### ✅ 已有的基础设施

1. **MySQL 持久化** (`messages.go`)
   - `chat_messages` 表存储所有消息
   - `GetMessages()` - 按 session_id 查询全部消息
   - `SaveMessages()` - 批量写入消息

2. **Redis 缓存层** (`cache.go`)
   - `GetOrLoad()` - 防雪崩/击穿/穿透
   - 分布式锁防止缓存击穿
   - TTL + 随机抖动防止雪崩

### ❌ 当前的问题

1. **消息历史未走缓存** - 每次 `GetMessages()` 都直接查 MySQL
2. **热点消息重复查询** - 活跃会话的消息被频繁读取，但没有缓存
3. **多副本一致性** - 没有缓存策略，副本间消息延迟

### 💡 用户建议的方向

**Redis + MySQL 分层** 是正确的！应该：
- Redis 存**热点数据**（最近会话、活跃上下文）
- MySQL 存**全量数据**（所有历史消息）

---

## 🏗️ 推荐的分层架构

### 架构图

```
┌─────────────────────────────────────────────────────────────┐
│  应用层 (Handler)                                             │
│  - GetSessionMessages(sessionId)                             │
│  - SaveMessage(sessionId, message)                           │
└──────────────────────┬──────────────────────────────────────┘
                       │
        ┌──────────────┴──────────────┐
        ↓                             ↓
┌──────────────────┐         ┌────────────────────┐
│  Redis (热数据)   │         │  MySQL (全量数据)  │
│  ───────────────  │         │  ────────────────  │
│ • 最近 N 条消息  │         │ • 所有消息        │
│ • 活跃会话       │         │ • 完整历史        │
│ • 上下文缓存     │         │ • 审计日志        │
│ • TTL: 1-24h    │         │ • 永久保存        │
└──────────────────┘         └────────────────────┘
        ↑                             ↑
        └─────────────────┬───────────┘
                          │
                    缓存层 (Cache)
                    • GetOrLoad
                    • 分布式锁
                    • 失效策略
```

---

## 📊 分层策略详解

### 1️⃣ Redis 层 - 热数据

**存储内容** (按优先级)

```
优先级 1 (必须缓存):
  └─ 最近 24 小时活跃会话的消息
     Key: "session:{sessionId}:messages:recent"
     Value: JSON array of last N messages
     TTL: 24 小时

优先级 2 (应该缓存):
  └─ 当前正在进行的对话上下文
     Key: "session:{sessionId}:context"
     Value: Last 5-10 messages (用于 prompt)
     TTL: 1 小时 (对话进行中自动延期)

优先级 3 (可选缓存):
  └─ 消息统计数据
     Key: "session:{sessionId}:stats"
     Value: { count, lastUpdate, tokenCount }
     TTL: 1 小时
```

### 2️⃣ MySQL 层 - 冷数据

**存储内容**

```
永久存储:
  └─ chat_messages 表
     - id, session_id, role, content, created_at, extra
     - 索引: (session_id, created_at)
     - 保留所有历史消息（永不删除）
```

### 3️⃣ 缓存策略

**读取流程**

```
GetSessionMessages(sessionId):
  1. 检查 Redis 是否有缓存 (Key: session:{id}:messages:recent)
     ├─ 命中 → 直接返回 (0ms)
     └─ 未命中 ↓
  2. 获取分布式锁 (防止 thundering herd)
  3. 从 MySQL 查询最近 N 条消息
  4. 写入 Redis (TTL: 24h + jitter)
  5. 返回结果
```

**写入流程**

```
SaveMessage(sessionId, message):
  1. 写入 MySQL (事务)
  2. 更新 Redis 缓存
     ├─ 追加到 session:{id}:messages:recent
     ├─ 更新 session:{id}:context (最新 10 条)
     └─ 更新 session:{id}:stats
  3. 发布 PubSub 事件 (多副本同步)
```

**失效策略**

```
缓存失效时机:
  1. 显式失效
     └─ 删除会话时: INVALIDATE session:{id}:messages:recent
  
  2. TTL 失效
     └─ 24 小时后自动过期
  
  3. 增量更新
     └─ 每条新消息，更新而不是整体重建
```

---

## 💻 代码实现建议

### 方案 A: 在 cache.go 基础上扩展

```go
// 新增：会话消息缓存
type MessageCache struct {
    cache *Cache  // 用现有的 cache.go
    db *sql.DB
}

func (mc *MessageCache) GetSessionMessages(
    ctx context.Context, 
    sessionId string,
    limit int,  // 最多返回多少条
) ([]MessageRow, error) {
    key := fmt.Sprintf("session:%s:messages:recent", sessionId)
    
    // 使用现有的 GetOrLoad，自动处理缓存和加载
    data, err := mc.cache.GetOrLoad(ctx, key, func(ctx context.Context) ([]byte, error) {
        // loader：从 MySQL 加载最近 N 条
        msgs, err := persistence.GetMessages(ctx, mc.db, sessionId)
        if err != nil {
            return nil, err
        }
        
        // 只缓存最近 limit 条，避免缓存过大
        if len(msgs) > limit {
            msgs = msgs[len(msgs)-limit:]
        }
        
        return json.Marshal(msgs)
    })
    
    if err != nil {
        return nil, err
    }
    
    var messages []MessageRow
    if err := json.Unmarshal(data, &messages); err != nil {
        return nil, err
    }
    return messages, nil
}

func (mc *MessageCache) SaveMessageAndUpdateCache(
    ctx context.Context,
    sessionId string,
    msg persistence.Message,
) error {
    // 1. 先写 MySQL
    if err := persistence.SaveMessages(ctx, mc.db, msg); err != nil {
        return err
    }
    
    // 2. 更新 Redis 缓存
    key := fmt.Sprintf("session:%s:messages:recent", sessionId)
    
    // 方式 A：失效缓存，下次读时重新加载
    // mc.cache.Invalidate(ctx, key)
    
    // 方式 B：增量更新缓存（更优雅）
    mc.appendMessageToCache(ctx, key, msg)
    
    // 3. 发布事件（通知其他副本）
    // 见下面 "多副本同步" 部分
    
    return nil
}

func (mc *MessageCache) appendMessageToCache(
    ctx context.Context,
    key string,
    newMsg persistence.Message,
) {
    // 读当前缓存
    data, _ := mc.cache.client.Get(ctx, key).Bytes()
    var msgs []MessageRow
    json.Unmarshal(data, &msgs)
    
    // 追加新消息
    msgs = append(msgs, MessageRow{
        SessionID: newMsg.SessionID,
        Role: newMsg.Role,
        Content: newMsg.Content,
        CreatedAt: time.Now(),
    })
    
    // 如果超过 limit，截断旧消息
    const maxCached = 100
    if len(msgs) > maxCached {
        msgs = msgs[len(msgs)-maxCached:]
    }
    
    // 写回 Redis
    data, _ = json.Marshal(msgs)
    mc.cache.client.Set(ctx, key, data, 24*time.Hour)
}
```

### 方案 B: 更细粒度的上下文缓存

```go
// 会话上下文缓存（用于 prompt）
type SessionContext struct {
    SessionID string
    LastMessages []MessageRow  // 最近 10 条
    Summary string             // 会话摘要（可选）
    TokenCount int             // 累积 token 数
    UpdatedAt time.Time
}

func (mc *MessageCache) GetSessionContext(
    ctx context.Context,
    sessionId string,
) (*SessionContext, error) {
    key := fmt.Sprintf("session:%s:context", sessionId)
    
    data, err := mc.cache.GetOrLoad(ctx, key, func(ctx context.Context) ([]byte, error) {
        // 从 MySQL 获取最近 10 条消息
        msgs, err := persistence.GetMessages(ctx, mc.db, sessionId)
        if err != nil {
            return nil, err
        }
        
        // 只取最后 10 条用于 prompt
        const contextSize = 10
        if len(msgs) > contextSize {
            msgs = msgs[len(msgs)-contextSize:]
        }
        
        context := &SessionContext{
            SessionID: sessionId,
            LastMessages: msgs,
            UpdatedAt: time.Now(),
        }
        
        return json.Marshal(context)
    })
    
    if err != nil {
        return nil, err
    }
    
    var context SessionContext
    if err := json.Unmarshal(data, &context); err != nil {
        return nil, err
    }
    return &context, nil
}
```

---

## 🔄 多副本同步

当有多个 lea-agent 副本时，需要保证缓存一致性：

### 方案：Redis PubSub

```go
// Publisher（消息写入副本）
func (mc *MessageCache) PublishMessageEvent(ctx context.Context, event MessageEvent) {
    mc.cache.client.Publish(ctx, "message:updates", json.Marshal(event))
}

// Subscriber（其他副本）
func (mc *MessageCache) SubscribeToMessages(ctx context.Context) {
    sub := mc.cache.client.Subscribe(ctx, "message:updates")
    defer sub.Close()
    
    for msg := range sub.Channel() {
        var event MessageEvent
        json.Unmarshal([]byte(msg.Payload), &event)
        
        // 失效本副本的缓存
        key := fmt.Sprintf("session:%s:messages:recent", event.SessionID)
        mc.cache.Invalidate(ctx, key)
    }
}
```

---

## 📈 性能对比

### 不用缓存（现状）
```
查询 1000 条消息历史：
  └─ MySQL 查询：50-100ms
  └─ 热点会话每秒 10 次查询：1000ms/秒
  
结论：高峰期数据库压力大
```

### 用 Redis + MySQL（建议）
```
查询 1000 条消息历史（缓存命中）：
  └─ Redis 查询：1-5ms
  └─ 热点会话每秒 10 次查询：50ms/秒（减少 95%）
  
缓存失效或首次查询：
  └─ MySQL 查询：50-100ms
  └─ Redis 写入：1-5ms
  
结论：热点数据快速响应，整体吞吐量提升 10-20 倍
```

---

## 🔧 实施步骤

### Phase 1: 基础集成（1-2 天）
1. ✅ 扩展 `cache.go`，添加 MessageCache
2. ✅ 实现 `GetSessionMessages()` 走缓存
3. ✅ 在 `SaveMessages()` 后更新缓存
4. ✅ 单测验证缓存正确性

### Phase 2: 优化（1 天）
1. ✅ 实现 `GetSessionContext()`（用于 prompt）
2. ✅ 添加增量更新而不是全量失效
3. ✅ 性能基准测试

### Phase 3: 多副本同步（1 天）
1. ✅ 实现 Redis PubSub 事件发布
2. ✅ 各副本订阅消息更新事件
3. ✅ 集成测试

### Phase 4: 监控告警（1 天）
1. ✅ 缓存命中率指标
2. ✅ MySQL 查询延迟监控
3. ✅ Redis 内存使用监控

---

## ⚠️ 注意事项

### 1. 内存管理
```
Redis 内存成本评估：
  • 假设平均活跃会话：500
  • 每个会话最近 100 条消息
  • 平均消息大小：200 字节
  • 总计：500 * 100 * 200 = 10 MB
  
建议：
  • 最多保留 1000 个会话的最近消息
  • 或按 50 MB 限额
  • 超出时按 LRU 淘汰
```

### 2. 一致性保证
```
可能的不一致场景：
  1. 缓存延迟：消息写入 MySQL 但还未到 Redis
  2. 副本不同步：A 副本更新了，B 副本还用旧缓存
  
解决方案：
  • 读 MySQL 时加版本号
  • 写入后 invalidate 而不是 set
  • 让下一个读请求触发重新加载
```

### 3. 故障恢复
```
Redis 挂掉时：
  • cache.go 已有降级逻辑 (line 59-61)
  • 会直接调用 loader，回源 MySQL
  • 性能下降但不会 brick

MySQL 挂掉时：
  • 返回缓存中的历史数据
  • 新消息无法写入
  • 应立即告警
```

---

## 总结

| 方面 | 当前 | 建议后 |
|------|------|--------|
| 热点读延迟 | 50-100ms | 1-5ms |
| 数据库查询频率 | 每秒 100+ | 每秒 5-10 |
| 内存占用 | 0 MB | ~50 MB |
| 多副本同步 | 手动清缓存 | 自动 PubSub |
| 故障恢复 | 无缓存，影响大 | 有缓存，影响小 |

**建议**: 按 Phase 1-4 逐步实施，优先做 Phase 1 和 2，Phase 3-4 可后续补充。

---

**版本**: 1.0.0  
**状态**: 待实施
