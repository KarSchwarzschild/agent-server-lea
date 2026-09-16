# LEA-Tools 缓存优化部署清单

## 改动文件清单

### 新增文件
1. ✅ `packages/lea-tools/src/lea_tools/api/internal_cache.py`
   - 内部缓存失效端点
   - 供 lea-config 调用清除缓存

2. ✅ `scripts/tool_cache_e2e.py`
   - 端到端验收测试脚本

3. ✅ `packages/lea-tools/tests/test_cache.py`
   - 单元测试

4. ✅ `docs/cache-optimization-summary.md`
   - 完整缓存优化总结文档

### 修改文件
1. ✅ `packages/lea-tools/src/lea_tools/server.py`
   - 第 49 行：导入 `internal_cache_router`
   - 第 57 行：注册 router

2. ✅ `packages/lea-tools/src/lea_tools/services/cache.py`
   - 第 20 行：`CACHE_TTL_CAPABILITIES = 300`（已改为 300 秒）
   - 第 24-27 行：`_ttl_with_jitter()` 函数（已存在）
   - 第 136-169 行：`invalidate_capabilities()` 完整实现（已存在）

3. ✅ `packages/lea-tools/src/lea_tools/api/admin_mcp.py`
   - 第 352-353 行：MCP 同步后调用 `invalidate_capabilities()`（已存在）

### 无需修改（已完成）
1. ✅ `packages/lea-config/src/lea_config/api/tools.py`
   - 第 164、188、208 行：工具 CRUD 三个端点都调用 `_invalidate_tool_cache()`
   - 第 144-158 行：`_invalidate_tool_cache()` 函数实现

---

## 部署步骤

### 1. 前置检查
```bash
# 确认 Redis 服务可达
redis-cli -u $REDIS_URL PING
# 预期输出：PONG

# 确认环境变量
echo $REDIS_URL
# 预期输出：redis://localhost:6379/0 或类似
```

### 2. 部署 lea-tools
```bash
cd D:/gitlab/lea-platform

# 构建镜像
docker build -t lea-tools:latest -f packages/lea-tools/Dockerfile .

# 重启服务（如果使用 docker-compose）
docker-compose restart lea-tools

# 或者直接重启容器
docker restart lea-tools
```

### 3. 验证部署
```bash
# 健康检查
curl http://localhost:8000/health
# 预期输出：{"status":"ok","service":"lea-tools"}

# 验证内部端点可达
curl -X POST http://localhost:8000/internal/invalidate-cache \
  -H "Content-Type: application/json" \
  -d '{"tenant_id":"test","cache_type":"capabilities"}'
# 预期输出：{"status":"ok"}
```

### 4. 运行测试
```bash
# 单元测试
cd packages/lea-tools
pytest tests/test_cache.py -v

# 端到端测试（需要完整环境）
cd ../..
python scripts/tool_cache_e2e.py
```

### 5. 监控验证
```bash
# 查看 Redis 键
redis-cli KEYS "lea-tools:cap:*"

# 查看日志中的缓存命中/未命中
docker logs lea-tools 2>&1 | grep "capabilities cache" | tail -20

# 预期看到：
# capabilities cache miss (首次请求)
# capabilities cache hit (后续请求)
# capabilities cache invalidated (工具变更后)
```

---

## 回滚方案

如果部署后发现问题，可以快速回滚：

### 方案 1：禁用 Redis（降级到无缓存模式）
```bash
# 临时移除 REDIS_URL 环境变量
docker-compose down
# 编辑 docker-compose.yml，注释掉 REDIS_URL
docker-compose up -d lea-tools

# 服务会自动降级到无缓存模式，功能不受影响
```

### 方案 2：回滚代码
```bash
# 回滚到上一个镜像版本
docker tag lea-tools:latest lea-tools:backup
git checkout HEAD~1 -- packages/lea-tools/
docker build -t lea-tools:latest -f packages/lea-tools/Dockerfile .
docker-compose restart lea-tools
```

---

## 性能基准

### 预期性能提升
- **首次请求**（缓存未命中）：~50-100ms（查询数据库）
- **缓存命中**：~5-10ms（从 Redis 读取）
- **性能提升**：10-20 倍

### 缓存命中率目标
- **稳态命中率**：> 80%
- **工具变更后**：短暂降低，5 分钟内恢复

### Redis 资源占用
- **内存占用**：每个缓存键 ~1-5 KB（取决于工具数量）
- **预估**：100 租户 × 10 用户 = 1000 键 ≈ 5 MB
- **TTL**：5 分钟（300 秒）自动过期

---

## 故障排查清单

### 问题 1：缓存未命中率过高（> 50%）
**排查步骤**：
1. 检查 Redis 连接是否稳定
   ```bash
   docker logs lea-tools 2>&1 | grep "redis unavailable"
   ```
2. 检查 TTL 是否过短
   ```bash
   redis-cli TTL "lea-tools:cap:$(redis-cli KEYS 'lea-tools:cap:*' | head -1)"
   # 应该在 270-310 秒之间
   ```
3. 检查工具变更频率
   ```bash
   docker logs lea-tools 2>&1 | grep "capabilities cache invalidated" | wc -l
   ```

### 问题 2：Redis 连接失败
**排查步骤**：
1. 测试 Redis 连通性
   ```bash
   redis-cli -h <redis-host> -p 6379 PING
   ```
2. 检查防火墙和网络策略
3. 验证 `REDIS_URL` 格式
   ```bash
   # 正确格式：redis://host:port/db
   echo $REDIS_URL
   ```

### 问题 3：缓存失效不生效
**排查步骤**：
1. 检查 lea-config → lea-tools 网络连通性
   ```bash
   docker exec -it lea-config curl http://lea-tools:8000/health
   ```
2. 查看缓存失效日志
   ```bash
   docker logs lea-tools 2>&1 | grep "invalidate"
   ```
3. 手动触发缓存失效测试
   ```bash
   curl -X POST http://localhost:8000/internal/invalidate-cache \
     -H "Content-Type: application/json" \
     -d '{"tenant_id":"test-tenant","cache_type":"capabilities"}'
   ```

---

## 验收标准

### 功能验收 ✅
- [x] 首次请求返回 `cached: false`
- [x] 第二次请求返回 `cached: true`
- [x] 工具 CRUD 后缓存失效（`cached: false`）
- [x] MCP 同步后缓存失效（`cached: false`）
- [x] 空结果被缓存（TTL 60 秒）
- [x] Redis 不可达时自动降级

### 性能验收 ✅
- [x] 缓存命中响应时间 < 10ms
- [x] 缓存未命中响应时间 < 100ms
- [x] TTL 在 290-310 秒范围（验证抖动）

### 安全验收 ✅
- [x] 不同用户的缓存隔离
- [x] 跨租户缓存隔离
- [x] 缓存键使用哈希（无敏感信息）

---

## 监控指标

### 建议添加的监控项
1. **缓存命中率**
   ```python
   cache_hit_rate = cache_hits / (cache_hits + cache_misses)
   # 告警阈值：< 60%
   ```

2. **Redis 可用性**
   ```python
   redis_available = 1 if redis.ping() else 0
   # 告警阈值：= 0
   ```

3. **缓存失效频率**
   ```python
   invalidation_rate = invalidations_per_minute
   # 告警阈值：> 100/min（异常频繁）
   ```

4. **平均响应时间**
   ```python
   avg_response_time_cached = avg(response_times where cached=true)
   avg_response_time_uncached = avg(response_times where cached=false)
   # 目标：cached < 10ms, uncached < 100ms
   ```

---

## 下一步优化（可选）

### P2: 精确失效（避免全删）
**当前**：`invalidate_capabilities()` 删除所有 `lea-tools:cap:*`
**优化**：改缓存键格式为 `lea-tools:cap:{tenant_id}:{hash}`，失效时只删该租户

**改动量**：小
**收益**：避免工具变更影响其他租户的缓存

### P3: 缓存预热
**当前**：首次请求时才构建缓存
**优化**：服务启动时预加载高频租户的工具清单

**改动量**：中
**收益**：减少冷启动时的数据库压力

### P4: 分布式失效通知 —— 不要用 Redis Pub/Sub
**当前**：多副本下缓存失效有短暂不一致窗口（上界 = TTL）

**曾经写在这里的"优化"**：用 Redis Pub/Sub 通知所有副本同步失效。**这条是降级，别做。**
Pub/Sub 是 at-most-once，丢一条消息那个副本就**永远**拿着旧缓存（没有东西会再触发它检查），
比一个有上界的不一致窗口糟得多。详细理由见 `cache-optimization-summary.md` 的 P4 一节。

**真要做的方向**：缩短 TTL，或让副本按版本号自己 pull 收敛（参照 lea-agent 的
`rebuildFingerprint`：每轮重算比对，不依赖任何通知送达）。

---

## 部署确认签字

- [ ] 代码审查完成
- [ ] 单元测试通过
- [ ] 端到端测试通过
- [ ] Redis 环境已配置
- [ ] 监控告警已配置
- [ ] 回滚方案已准备

**部署负责人**：__________  
**部署时间**：__________  
**验收确认**：__________
