# 🎯 快速参考卡片

> **停更提示（2026-10-13）**：本文停更在 2026-09-16。下面"关键文件位置"里列的
> `CURRENT_STATUS.md`、`DEPLOYMENT_CHECKLIST.md`、`FINAL_SUMMARY.md` 等已移到
> `docs/archive/`。
>
> 当前实际在用的部署路径：
> - 测试服 → `docs/TEST_SERVER_DEPLOY.md`
> - 生产 CCE/k8s → `infra/k8s/README.md`

## 📂 关键文件位置

### 配置文件（新建）
```
✅ infra/prometheus/prometheus.yml              (Prometheus 采集配置)
✅ infra/prometheus/alerts.yml                  (告警规则)
✅ infra/grafana/provisioning/datasources/prometheus.yml
✅ infra/grafana/provisioning/dashboards/system-health.json
✅ infra/docker-compose.verify.yml             (已更新，加入 Prometheus + Grafana)
```

### 文档文件（新建）
```
✅ CURRENT_STATUS.md                            (全景状态 + 时间表)
✅ DEPLOYMENT_CHECKLIST.md                      (5 个 Phase + 检查项)
✅ DEPLOYMENT_GUIDE.md                          (完整部署实施指南)
✅ FINAL_SUMMARY.md                             (本次会话总结)
✅ QUICK_REFERENCE.md                           (本文件)
```

### 代码文件（已验证）
```
✅ packages/lea-agent/internal/runtime/config_loader.go:195-226
✅ packages/lea-agent/internal/runtime/platform_tools.go:473-486
✅ packages/lea-agent/internal/breaker/breaker.go
✅ packages/lea-agent/internal/persistence/sessions.go
✅ packages/lea-agent/internal/router/session_active_agent.go
✅ packages/lea-agent/internal/session/manager.go
✅ scripts/expert_switch_e2e.py (378 lines)
✅ scripts/test_production_readiness.py
```

---

## ⚡ 常用命令速查

### 本地验证（30 min）
```bash
# 启动验证栈（包含 Prometheus + Grafana）
docker-compose -f infra/docker-compose.verify.yml up -d

# 等待服务启动
sleep 60

# 验证所有服务就绪
curl -s http://localhost:8080/nginx-health
curl -s http://localhost:9090/-/healthy
curl -s http://localhost:3000/api/health

# 查看 Prometheus 采集状态
curl -s http://localhost:9090/api/v1/targets | jq '.data.activeTargets | length'
# 预期: 4（或更多，如果配了 Redis/MySQL）

# 打开 Grafana 仪表板
# 浏览器: http://localhost:3000
# 用户: admin / 密码: admin
# 选择仪表板 > "LEA Platform — 系统健康"
```

### 部署前验证（DBA）
```bash
# 检查 MySQL 迁移
mysql -h <host> -u <user> -p lea << EOF
SHOW INDEX FROM chat_sessions WHERE Key_name = 'idx_sessions_user_tenant';
SHOW INDEX FROM chat_messages WHERE Key_name = 'idx_messages_created_expired';
DESC chat_sessions;
EOF

# 预期：
# - 两个索引都存在
# - chat_sessions 有 active_agent_id 列
```

### 部署前验证（DevOps）
```bash
# 检查 Redis
redis-cli -h <host> -p 6379 PING
redis-cli -h <host> --latency 100    # 预期 < 10ms
redis-cli -h <host> INFO memory

# 检查 Nginx 限流
grep -n "limit_req" infra/nginx/lea-platform.conf

# 验证部署
docker-compose exec nginx nginx -t
```

### 灰度部署（SRE）
```bash
# 方式 1：Kubernetes
kubectl set image deployment/lea-agent \
  lea-agent=lea-agent:v$(git rev-parse --short HEAD) -n default

# 方式 2：Nginx 权重切换
# 编辑 infra/nginx/lea-platform.conf
# 改 upstream 权重为 5:95，然后：
docker-compose exec nginx nginx -s reload

# 方式 3：Docker Compose
docker-compose up -d --scale lea-agent=5  # 扩到 5 个副本

# 监控部署
curl -s http://localhost:9090/api/v1/alerts | jq '.data | length'
# 预期：0（无告警）

# 检查关键指标
curl -s 'http://localhost:9090/api/v1/query?query=lea_agent_circuit_breaker_state'
# 预期：state <= 1（Closed 或 Half-Open，不能是 Open）
```

### 回滚（紧急）
```bash
# Kubernetes 回滚
kubectl rollout undo deployment/lea-agent -n default

# 或 Nginx 权重恢复
# 编辑 infra/nginx/lea-platform.conf 改回 upstream
docker-compose exec nginx nginx -s reload

# 验证回滚
curl -s http://localhost:8200/health
```

### E2E 测试
```bash
# 运行真链路验收
LEA_AGENT_URL=http://127.0.0.1:8100 \
  python scripts/expert_switch_e2e.py

# 预期：All 7 scenarios PASSED ✅

# 性能基准测试
wrk -t 4 -c 10 -d 60s http://localhost:8200/api/chat/stream

# 记录结果到
docs/PERFORMANCE_BASELINE.md
```

---

## 🔔 关键告警规则

| 告警名 | 触发条件 | 严重度 | 行动 |
|--------|---------|--------|------|
| `CircuitBreakerOpen` | 熔断器 > 1 for 1m | 🔴 Critical | 立即调查 KB/Memory 服务 |
| `HighErrorRate5xx` | 5xx 率 > 5% for 5m | 🔴 Critical | 检查服务日志、考虑回滚 |
| `CacheInvalidationRateHigh` | 缓存失效 > 10% for 10m | 🟡 Warning | 检查 Redis 连接 |
| `DBQueryLatencyP95High` | P95 > 1s for 5m | 🟡 Warning | 检查数据库索引 |
| `PrometheusScrapeFailed` | 服务 DOWN for 2m | 🔴 Critical | 检查服务是否崩溃 |

**处理流程**:
1. 🔴 Critical 告警 → 立即联系 SRE
2. 🟡 Warning 告警 → 5 分钟内调查
3. 连续 3 个 Critical 告警 → 考虑回滚

---

## 📋 部署前核对表（打印用）

### Phase 1: 基础设施 (30 min)
```
☐ MySQL 三个迁移都已应用（029、030、023）
☐ Redis 连接正常，延迟 < 10ms
☐ Nginx 限流规则已生效
☐ 所有网络连接测试通过
```

### Phase 2: 代码 (30 min)
```
☐ 4 个服务编译成功（go build / python -m py_compile）
☐ 4 个 /metrics 端点都返回 200 + Prometheus 格式
☐ 缓存集成验证（grep GetOrLoad）
☐ 降级逻辑验证（grep fail-soft）
```

### Phase 3: 可观测性 (60 min)
```
☐ Prometheus 配置通过 dry-run
☐ 告警规则 YAML 有效
☐ Grafana 仪表板加载成功
☐ 数据实时更新（刷新后有变化）
```

### Phase 4: 部署计划 (60 min)
```
☐ 灰度计划文档完成
☐ 回滚脚本已测试
☐ 变更批准流程已启动
☐ SRE 已确认值班
```

### Phase 5: 环境 (15 min)
```
☐ REDIS_URL 已配置（非 127.0.0.1）
☐ DATABASE_URL 已配置
☐ LEA_INTERNAL_TOKEN 三个服务一致
☐ TTL / 容量参数合理
```

### 部署中
```
☐ 5% 部署 + 5 min 监控 ✓
☐ 10% 部署 + 15 min 监控 ✓
☐ 25% 部署 + 15 min 监控 ✓
☐ 100% 部署 + 30 min 监控 ✓
☐ 无任何 Critical 告警
```

### 部署后
```
☐ E2E 测试 7 个场景全部 PASS
☐ 性能基准已测定并存档
☐ 文档已发布
☐ 团队已培训
```

---

## 🔗 文件之间的关系

```
CURRENT_STATUS.md (全景)
    ↓
DEPLOYMENT_GUIDE.md (详细步骤)
    ↓
DEPLOYMENT_CHECKLIST.md (执行清单)
    ↓
执行部署...
    ↓
scripts/expert_switch_e2e.py (验收)
    ↓
docs/PERFORMANCE_BASELINE.md (记录结果)
```

---

## 📞 紧急联系方式（部署当天）

| 问题 | 联系人 | 电话/Slack | 优先级 |
|------|--------|----------|--------|
| 数据库问题 | DBA | - | 最高 |
| 服务宕机 | SRE Lead | - | 最高 |
| 网络/基础设施 | DevOps | - | 高 |
| 性能问题 | 架构师 | - | 高 |
| 其他问题 | 当班 SRE | - | 中 |

**应急热线**: [填入你的值班电话]  
**应急 Slack**: #lea-emergency

---

## ✨ 完成状态一览

```
✅ 代码实现                  100% 完成
   ├─ P0 (8 个)             ✅
   ├─ P1 (3 个)             ✅
   ├─ Layer 1 (完整)        ✅
   └─ 单元测试              ✅

✅ 部署配置                  100% 准备
   ├─ Prometheus            ✅
   ├─ Grafana 仪表板        ✅
   ├─ 告警规则              ✅
   └─ 灰度部署计划          ✅

✅ 文档                      100% 完成
   ├─ 部署指南              ✅
   ├─ 检查清单              ✅
   ├─ 故障排查              ✅
   └─ 快速参考              ✅

✅ E2E 脚本                  100% 准备
   ├─ 7 个验收场景          ✅
   ├─ 真链路测试            ✅
   └─ 历史完整性验证        ✅

🟡 部署执行                  待启动
   ├─ Phase 1-5 验证        📋 清单已准备
   ├─ 灰度部署              📋 计划已准备
   └─ E2E 验收              📋 脚本已准备

🟡 部署后验收                待执行
   ├─ 真链路测试            ⏳ 1 天
   ├─ 性能基准              ⏳ 3 天
   └─ 文档发布              ⏳ 2 天
```

---

## 🎯 下一步行动

1. **立即** (30 min)
   - 启动本地验证栈
   - 验证 Prometheus + Grafana 就绪

2. **今天** (1-2 h)
   - 与 DBA/DevOps/SRE 确认 Phase 1-5 条件
   - 选择部署模式（推荐严格模式）

3. **明天** (4 h)
   - 执行灰度部署 5% → 100%
   - 持续监控告警

4. **本周** (9 h)
   - E2E 测试
   - 性能基准
   - 文档发布

---

## 📚 相关文档

| 文档 | 用途 | 读者 |
|------|------|------|
| `CURRENT_STATUS.md` | 全景状态 + 优先级 | 所有人 |
| `DEPLOYMENT_GUIDE.md` | 详细步骤 | SRE / DevOps |
| `DEPLOYMENT_CHECKLIST.md` | 执行清单 | 部署团队 |
| `FINAL_SUMMARY.md` | 本次会话总结 | 决策者 |
| `README.md` (各服务) | 服务说明 | 开发者 |

---

**预期部署周期**: 3-4 天  
**风险评级**: 🟢 低  
**推荐模式**: 严格模式  

**准备好了吗？让我们开始吧！🚀**
