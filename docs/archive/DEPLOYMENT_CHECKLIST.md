# 部署前完整清单

**预计完成时间**: 2.5-3 小时（如所有条件已就位）

---

## 🔴 Critical — 部署前必须完成

### Phase 1: 基础设施验证 (30 min)

#### 1.1 MySQL 验证
```bash
# 连接测试
mysql -h <host> -u <user> -p -e "SELECT 1"

# 检查迁移 029（会话快查）
mysql -e "SHOW INDEX FROM chat_sessions WHERE Key_name = 'idx_sessions_user_tenant';"
# 预期: 1 行，Key_name='idx_sessions_user_tenant'，Columns='user_id','tenant_id','project_id'

# 检查迁移 030（消息过期扫）
mysql -e "SHOW INDEX FROM chat_messages WHERE Key_name = 'idx_messages_created_expired';"
# 预期: 1 行，Key_name='idx_messages_created_expired'，Columns='created_at','is_expired'

# 检查迁移 023（激活 agent）
mysql -e "DESC chat_sessions;" | grep active_agent_id
# 预期: active_agent_id | int(11) | NO | MUL | 0
```

**✅ 通过条件**:
- [ ] 三个迁移都已应用（索引存在）
- [ ] 无 InnoDB 错误
- [ ] 连接延迟 < 50ms

---

#### 1.2 Redis 验证
```bash
# 连接测试
redis-cli -h <host> -p 6379 PING
# 预期: PONG

# 读写延迟测试
redis-cli -h <host> --latency 100
# 预期: 平均延迟 < 10ms（若 > 50ms 需检查网络）

# 容量检查
redis-cli -h <host> INFO memory
# 找 used_memory_human；对比预期 = agent_count × tenant_count × 3 × 1KB
# 例：100 agents × 10 tenants × 3 × 1KB = ~3MB，需预留 >= 10MB

# 故障测试（可选，部署后才做）
# 1. 关掉 Redis
# 2. 请求 /api/chat/stream 发一条消息
# 预期: 对话继续正常（降级到直接回源，无缓存）
```

**✅ 通过条件**:
- [ ] PING 返回 PONG
- [ ] 读写延迟 < 10ms
- [ ] 可用容量 >= 计算值的 3 倍
- [ ] （部署后）Redis 故障时对话继续工作

---

#### 1.3 Nginx 验证
```bash
# 检查限流配置
grep -n "limit_req_zone\|limit_req" infra/nginx/lea-platform.conf

# 验证重启
sudo systemctl reload nginx
# 或
docker-compose restart nginx

# 测试限流
ab -n 1000 -c 100 http://<nginx>/api/metrics
# 预期: 部分请求返回 429（Too Many Requests）
```

**✅ 通过条件**:
- [ ] 三层限流规则都存在（IP/端点/文件上传）
- [ ] Nginx 重载无错误
- [ ] 压测能触发 429

---

### Phase 2: 代码验证 (30 min)

#### 2.1 编译与依赖
```bash
# lea-agent
cd packages/lea-agent
go mod tidy
go build -o /tmp/lea-agent ./cmd/server/main.go
# 预期: 编译成功，无 error

# lea-config
cd packages/lea-config
pip install -r requirements.txt
python -m py_compile src/lea_config/server.py
# 预期: 无语法错误

# lea-rag
cd packages/lea-rag
pip install -r requirements.txt
python -m py_compile src/lea_rag/tasks.py
# 预期: 无语法错误

# lea-tools
cd packages/lea-tools
pip install -r requirements.txt
python -m py_compile src/lea_tools/server.py
# 预期: 无语法错误
```

**✅ 通过条件**:
- [ ] 所有 4 个服务编译无错
- [ ] 无依赖版本冲突
- [ ] 无缺失的 .sql 迁移文件

---

#### 2.2 指标端点验证
```bash
# 启动 docker-compose
docker-compose -f infra/docker-compose.verify.yml up -d

# 等待 30 秒让服务启动
sleep 30

# 检查各服务 /metrics 端点
for svc in lea-agent:8200 lea-config:8500 lea-rag:8300 lea-tools:8400; do
  echo "=== $svc ==="
  curl -s http://localhost:${svc#*:}/metrics | head -20
done

# 预期: 每个服务都返回 Prometheus 格式的指标（以 # HELP 开头）
```

**✅ 通过条件**:
- [ ] 4 个服务都返回 200 OK
- [ ] 都是 Prometheus 格式（有 # HELP / # TYPE）
- [ ] 没有 500 错误

---

#### 2.3 缓存集成验证
```bash
# 检查代码
grep -n "cache.Get\|cache.Set\|GetOrLoad" packages/lea-agent/internal/runtime/config_loader.go | head -10

# 预期: 3+ 行代码调用缓存方法
```

**✅ 通过条件**:
- [ ] config_loader.go 有 GetOrLoad 模式
- [ ] 缓存键包含 tenant 隔离（`agent:{id}:{tenant}`）
- [ ] 有 nil 检查（Redis 不可用时 fallback）

---

#### 2.4 降级逻辑验证
```bash
# 检查 KB/Memory/Tool 降级
grep -n "IsError: false\|fail-soft" packages/lea-agent/internal/runtime/platform_tools.go

# 预期: 3+ 处 fail-soft 返回
```

**✅ 通过条件**:
- [ ] KBSearchTool 有降级返回
- [ ] MemorySearchTool 有降级返回
- [ ] MemoryAddTool 有降级返回

---

### Phase 3: Prometheus + 告警配置 (60 min)

#### 3.1 创建 Prometheus 配置
**文件**: `infra/prometheus/prometheus.yml`

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s
  external_labels:
    cluster: 'lea-platform'
    environment: 'staging'  # 改成 'production' 再上线

scrape_configs:
  - job_name: 'lea-agent'
    static_configs:
      - targets: ['localhost:8200']
    metrics_path: '/metrics'

  - job_name: 'lea-config'
    static_configs:
      - targets: ['localhost:8500']
    metrics_path: '/metrics'

  - job_name: 'lea-rag'
    static_configs:
      - targets: ['localhost:8300']
    metrics_path: '/metrics'

  - job_name: 'lea-tools'
    static_configs:
      - targets: ['localhost:8400']
    metrics_path: '/metrics'

rule_files:
  - 'alerts.yml'
```

**✅ 验证**:
```bash
# 验证 YAML 语法
docker run -v $(pwd)/infra/prometheus:/etc/prometheus prom/prometheus:latest \
  prometheus --config.file=/etc/prometheus/prometheus.yml --dry-run

# 预期: "config file is valid"
```

---

#### 3.2 创建告警规则
**文件**: `infra/prometheus/alerts.yml`

```yaml
groups:
  - name: lea-platform
    interval: 30s
    rules:
      # 熔断器打开
      - alert: CircuitBreakerOpen
        expr: lea_agent_circuit_breaker_state > 1
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "KB/Memory 熔断器打开"
          description: "{{ $labels.instance }} 熔断器已打开，服务降级中"

      # 5xx 错误率过高
      - alert: HighErrorRate
        expr: |
          (
            sum(rate(lea_agent_http_requests_total{status=~"5.."}[5m])) 
            / 
            sum(rate(lea_agent_http_requests_total[5m]))
          ) > 0.05
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "lea-agent 5xx 错误率 > 5%"
          description: "当前错误率: {{ $value | humanizePercentage }}"

      # 缓存失效率过高
      - alert: CacheInvalidationRate
        expr: |
          (
            sum(rate(lea_config_agent_config_loads_total{status="cache_miss"}[5m]))
            /
            sum(rate(lea_config_agent_config_loads_total[5m]))
          ) > 0.1
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Agent 配置缓存失效率 > 10%"
          description: "当前失效率: {{ $value | humanizePercentage }}"

      # 数据库查询延迟过高
      - alert: DBQueryLatency
        expr: histogram_quantile(0.95, lea_config_db_query_duration_seconds) > 1
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "数据库查询 P95 延迟 > 1 秒"
          description: "当前 P95: {{ $value | humanizeDuration }}"
```

**✅ 验证**:
```bash
# 检查 YAML 语法
yamllint infra/prometheus/alerts.yml

# 预期: 无错误
```

---

### Phase 4: 灰度部署计划 (60 min)

**文件**: `DEPLOYMENT_PLAN.md`

#### 4.1 金丝雀步骤

```
第 1 步: 5% 流量 (5 min 监控)
  ├─ 方式: Kubernetes 金丝雀部署 / Nginx 权重 5:95
  ├─ 监控: 查 Prometheus 是否有告警
  ├─ 回滚条件: P99 延迟 +20% 或 5xx 率 > 1%
  └─ 决策: 无告警 → 继续；有告警 → 立即回滚

第 2 步: 10% 流量 (15 min 监控)
  ├─ 方式: 10:90
  ├─ 监控: 同上
  ├─ 回滚条件: 同上
  └─ 决策: 无告警 → 继续；有告警 → 立即回滚

第 3 步: 25% 流量 (15 min 监控)
  ├─ 方式: 25:75
  ├─ 监控: 同上
  ├─ 回滚条件: 同上
  └─ 决策: 无告警 → 继续；有告警 → 立即回滚

第 4 步: 100% 流量 (30 min 监控)
  ├─ 方式: 全量切换
  ├─ 监控: 密切关注所有告警
  ├─ 回滚条件: 同上
  └─ 完成: 30 min 无告警 → 部署完成
```

#### 4.2 健康检查标准

```
关键指标:
  ✓ P99 延迟: 基线 ± 20%（变化 > 20% 为异常）
  ✓ 5xx 错误率: < 1%（基线通常 0.1-0.5%）
  ✓ 熔断器状态: Closed 或 Half-Open（Open 为异常）
  ✓ 缓存命中率: >= 70%（低于此值表明 Redis 故障）
  ✓ 数据库连接池: 使用率 < 80%

告警自动回滚条件:
  - CircuitBreakerOpen 且持续 > 2 min
  - HighErrorRate 告警触发
  - 任何服务 DOWN 超过 1 min
```

#### 4.3 回滚流程

```
Kubernetes 回滚:
  kubectl rollout undo deployment/lea-agent -n default
  kubectl rollout undo deployment/lea-config -n default
  kubectl rollout undo deployment/lea-rag -n default
  kubectl rollout undo deployment/lea-tools -n default

Nginx 权重回滚:
  # 编辑 lea-platform.conf，改回 upstream 权重
  # 重载 Nginx
  docker-compose exec nginx nginx -s reload

验证回滚成功:
  # 等 30 s，检查告警是否消退
  # 查询 Prometheus: CircuitBreakerOpen 是否解除
```

**✅ 通过条件**:
- [ ] 灰度计划已获架构师/PM 批准
- [ ] 回滚脚本已测试（在测试环境演练）
- [ ] 告警规则已在 Prometheus 配置里
- [ ] 随叫随到的 SRE 已确认值班

---

### Phase 5: 环境变量检查 (15 min)

```bash
# 检查 .env 或 docker-compose 配置
cat infra/.env | grep -E "REDIS_URL|DATABASE_URL|LEA_AGENT_DB|LEA_AGENT_SESSION_TTL"

# 预期配置:
# REDIS_URL=redis://<host>:6379/0
# DATABASE_URL=mysql://user:pass@<host>:3306/lea
# LEA_AGENT_DB=mysql  (或 off 如果禁用持久化)
# LEA_AGENT_SESSION_TTL=30m
# LEA_AGENT_MAX_SESSIONS=500
```

**✅ 通过条件**:
- [ ] REDIS_URL 已配置
- [ ] DATABASE_URL 已配置
- [ ] TTL/容量 参数合理

---

## 🟡 Important — 部署后 1 周内

### Phase 6: E2E 测试执行 (4 hours)

```bash
# 启动所有服务
docker-compose -f infra/docker-compose.verify.yml up -d

# 等待服务稳定
sleep 60

# 运行 E2E 测试
LEA_AGENT_URL=http://127.0.0.1:8100 python scripts/expert_switch_e2e.py

# 预期: All 7 scenarios passed ✅
```

**✅ 通过条件**:
- [ ] 7 个场景全部 PASS
- [ ] 无 FAIL
- [ ] 历史完整性验证通过

---

### Phase 7: 性能基准测试 (3 hours)

```bash
# 安装压测工具
brew install wrk  # macOS
apt-get install wrk  # Ubuntu
# 或用 Apache Bench: sudo apt-get install apache2-utils

# lea-agent 新建会话
wrk -t 4 -c 10 -d 60s -s /tmp/create_session.lua http://127.0.0.1:8100/api/sessions

# lea-agent 发消息（需要已有会话）
wrk -t 4 -c 10 -d 60s -s /tmp/send_message.lua http://127.0.0.1:8100/api/chat/stream

# lea-config 缓存命中
wrk -t 4 -c 10 -d 60s http://127.0.0.1:8500/internal/agent-config/1

# 预期结果记录到: docs/PERFORMANCE_BASELINE.md
```

**✅ 通过条件**:
- [ ] 所有端点 P95 都在预期内
- [ ] 无 500 错误
- [ ] 基准数据已归档

---

### Phase 8: 部署文档 (2 hours)

**文件**: `docs/DEPLOYMENT_GUIDE.md`

内容:
- [ ] 部署前清单（本文）
- [ ] 部署步骤（分阶段）
- [ ] 故障排查指南
- [ ] 监控告警清单
- [ ] 回滚流程
- [ ] 应急联系方式

---

## ✅ 最后验收

### 部署前核对表

```
基础设施 (Phase 1):
  ☐ MySQL 三个迁移都已应用
  ☐ Redis 连接正常，延迟 < 10ms
  ☐ Nginx 限流规则已生效

代码 (Phase 2):
  ☐ 4 个服务编译无错
  ☐ 4 个 /metrics 端点返回 200
  ☐ 缓存集成确认
  ☐ 降级逻辑确认

可观测性 (Phase 3):
  ☐ prometheus.yml 已创建且通过 dry-run
  ☐ alerts.yml 已创建且语法正确
  ☐ Prometheus 能采集到 4 个服务的指标

部署计划 (Phase 4):
  ☐ 灰度计划文档已完成
  ☐ 健康检查标准已定义
  ☐ 回滚脚本已测试
  ☐ 变更审批已通过

环境 (Phase 5):
  ☐ 所有环境变量已配置
  ☐ 密钥/证书已就位
  ☐ DNS/TLS 已配置

部署后验收 (Phase 6-8):
  ☐ E2E 测试全部通过
  ☐ 性能基准已测定
  ☐ 部署文档已发布
  ☐ 团队已培训
```

### 上线决策

✅ **可以部署当且仅当**:
- Phase 1-5 的所有检查都通过
- 没有红色告警在运行
- 灰度计划已批准
- SRE 已确认值班

❌ **不能部署如果**:
- 任何一项编译/部署测试失败
- 环境变量不完整
- 没有 SRE 值班
- 3 小时内还有其他上线计划

---

## 📞 应急联系

| 角色 | 姓名 | 电话 | 备注 |
|------|------|------|------|
| SRE Lead | - | - | 值班时段 |
| 架构师 | - | - | 决策权 |
| DBA | - | - | 迁移+回滚 |
| DevOps | - | - | 基础设施 |

---

**预计总耗时**: 2.5-3 小时（如条件都已就位）

**下一步**: 按 Phase 顺序执行，每个 Phase 都必须 100% 通过才能进入下一个。
