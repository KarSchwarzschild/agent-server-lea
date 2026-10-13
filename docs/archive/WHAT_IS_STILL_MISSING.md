# ❌ 现在还缺什么 — 完整缺口分析

**分析日期**: 2026-09-15  
**状态**: 代码 100% 就绪 | 配置 95% 就绪 | 部署执行 0% 就绪

---

## 🔴 Critical（必须做，不做不能上线）

### 1. Phase 1-5 部署前验证（需要分工执行）⏳
**负责**: DBA + DevOps + SRE  
**预计时间**: 2.5 小时  
**文件**: `DEPLOYMENT_CHECKLIST.md`

| Phase | 检查项 | 负责人 | 风险 |
|-------|--------|--------|------|
| **Phase 1** | MySQL 迁移 029/030/023 已应用？ | DBA | 🔴 高 |
| **Phase 1** | Redis 连接正常、延迟 < 10ms？ | DevOps | 🔴 高 |
| **Phase 1** | Nginx 限流规则已生效？ | DevOps | 🟡 中 |
| **Phase 2** | 4 个服务都能编译？ | CI/自动化 | 🟡 中 |
| **Phase 2** | 4 个 /metrics 端点都返回 200？ | DevOps | 🟡 中 |
| **Phase 3** | Prometheus 能采集 4 个 targets？ | SRE | 🟡 中 |
| **Phase 3** | 10 个告警规则都已加载？ | SRE | 🟡 中 |
| **Phase 4** | 灰度计划已批准？ | 架构师 + PM | 🔴 高 |
| **Phase 5** | 环境变量都已配置？ | DevOps | 🔴 高 |

**缺了什么**：这些是"活动"而不是"产物"，需要团队各部分实际执行一遍。已有清单，缺的是执行结果。

**什么时候做**：部署前 2 天开始，最迟部署前 1 小时完成。

---

### 2. 生产环境参数配置文件 📝
**负责**: DevOps  
**预计时间**: 1 小时  
**创建**: `.env.production`（生产环境变量文件）

**需要配置的关键参数**：
```bash
# Redis
REDIS_URL=redis://<prod-host>:6379/0           # ← 必须不是 127.0.0.1
REDIS_PASSWORD=<secure-password>
REDIS_POOL_SIZE=50

# MySQL
DATABASE_URL=mysql://user:pass@<prod-host>:3306/lea
DATABASE_POOL_MIN=10
DATABASE_POOL_MAX=50

# 缓存 TTL
LEA_AGENT_SESSION_TTL=30m
LEA_CONFIG_CACHE_TTL=15m
LEA_KB_CACHE_TTL=1h

# 熔断器参数
CIRCUIT_BREAKER_MAX_FAILURES=5
CIRCUIT_BREAKER_TIMEOUT=60s

# Celery
CELERY_BROKER_URL=redis://<prod-host>:6379/1
CELERY_RESULT_BACKEND=redis://<prod-host>:6379/2

# 服务间认证
LEA_INTERNAL_TOKEN=<long-random-secret>         # 三个服务必须相同

# Prometheus
PROMETHEUS_PORT=9090
GRAFANA_ADMIN_PASSWORD=<secure-password>

# 日志级别
LOG_LEVEL=info                                  # 生产改为 info 或 warn
```

**缺了什么**：没有生产环境的具体参数值（host、密码、容量规划）。

**什么时候做**：部署前 1 天，由 DevOps 根据生产环境现状填充。

---

### 3. 数据库迁移执行确认 ✅/❌
**负责**: DBA  
**预计时间**: 30 分钟  
**执行**: 应用三个迁移到生产库

**需要执行的 SQL 迁移**：
```sql
-- 迁移 023：active_agent_id 列
ALTER TABLE chat_sessions
  ADD COLUMN active_agent_id INT NOT NULL DEFAULT 0;

-- 迁移 029：会话快查索引
CREATE INDEX idx_sessions_user_tenant 
  ON chat_sessions(user_id, tenant_id, created_at);

-- 迁移 030：消息过期扫描索引
CREATE INDEX idx_messages_created_expired
  ON chat_messages(created_at, expires_at);
```

**验证**：
```bash
mysql -h <prod-host> -u <user> -p lea << EOF
SHOW INDEX FROM chat_sessions WHERE Key_name IN ('idx_sessions_user_tenant', 'PRIMARY');
SHOW INDEX FROM chat_messages WHERE Key_name = 'idx_messages_created_expired';
DESC chat_sessions;
EOF
```

**缺了什么**：这三个迁移需要在**生产库**上真实执行一遍，不是在本地。

**什么时候做**：部署前 1 天，DBA 备份后执行。

---

### 4. Prometheus + Grafana 生产部署 🏗️
**负责**: SRE  
**预计时间**: 2 小时  
**现状**: 有本地验证栈（`docker-compose.verify.yml`），需要部署到真实环境

**两种部署方式**：

#### 方式 A：Docker Compose（小规模）
```bash
# 将配置文件复制到服务器
scp infra/prometheus/prometheus.yml <server>:/data/prometheus/
scp infra/prometheus/alerts.yml <server>:/data/prometheus/
scp infra/grafana/provisioning/ <server>:/data/grafana/

# 编辑 infra/docker-compose.yml
# 加入 Prometheus 和 Grafana 服务
# 然后部署
docker-compose -f infra/docker-compose.yml up -d prometheus grafana
```

#### 方式 B：Kubernetes（大规模）
```bash
# 需要创建：
# - ConfigMap for prometheus.yml / alerts.yml
# - PersistentVolume for 数据存储
# - Service 暴露端口
# - Deployment for Prometheus Pod
# - Deployment for Grafana Pod
```

**缺了什么**：
- Kubernetes YAML（如果生产用 K8s）
- 存储配置（Prometheus 数据持久化）
- 网络策略（谁能访问 Grafana/Prometheus）

**什么时候做**：Phase 3 验证时完成（部署前 2 天）。

---

### 5. 灰度部署计划最终批准 ✍️
**负责**: 架构师 + PM  
**预计时间**: 1 小时  
**现状**: 有详细计划（`DEPLOYMENT_GUIDE.md` 步骤 2-5），缺形式批准

**需要确认的**：
- [ ] 5% 金丝雀的具体副本数/权重
- [ ] 每个阶段的监控告警门槛（什么时候回滚）
- [ ] 回滚流程和授权（谁能决策回滚）
- [ ] 部署时间窗口（避开业务高峰）
- [ ] 应急联系方式和值班人员

**缺了什么**：正式的"部署计划审批单"或"变更单"（如果公司有 CAB）。

**什么时候做**：部署前 1 周内完成审批。

---

## 🟡 Important（必须做，但可以在部署后 1 周内完成）

### 6. E2E 真链路测试执行 🧪
**负责**: QA  
**预计时间**: 4 小时  
**脚本**: `scripts/expert_switch_e2e.py`

**7 个必须通过的场景**：
1. ✅/❌ 首消息带 agent_id → 人格 marker 验证
2. ✅/❌ 中途切换 → 历史完整性 + marker 变化
3. ✅/❌ 取消（PUT 0）→ 回到默认人格
4. ✅/❌ 多副本自愈 → 指纹一致性
5. ✅/❌ 专家团边界 → 链/单切换历史兼容
6. ✅/❌ 失效兜底 → 删除的 agent 不 brick
7. ✅/❌ 两层正交 → 专家 + attachments 共存

**执行命令**：
```bash
LEA_AGENT_URL=http://prod:8100 python scripts/expert_switch_e2e.py
# 预期: All 7 scenarios PASSED ✅
```

**缺了什么**：没有部署后的真实执行结果（现在只是代码存在）。

**什么时候做**：部署完成后第 1 个工作日。

---

### 7. 性能基准测试 📊
**负责**: DevOps / QA  
**预计时间**: 3 小时  
**产出**: `docs/PERFORMANCE_BASELINE.md`

**需要测试的端点**（用 wrk / Apache Bench）：
```bash
# 1. 聊天流端点
wrk -t 4 -c 50 -d 300s http://prod:8200/api/chat/stream

# 2. 配置加载
wrk -t 2 -c 20 -d 300s http://prod:8500/api/config/<id>

# 3. KB 搜索
wrk -t 2 -c 20 -d 300s http://prod:8300/api/search

# 4. 工具执行
wrk -t 2 -c 20 -d 300s http://prod:8400/api/execute

# 5. 指标采集
wrk -t 1 -c 10 -d 300s http://prod:8200/metrics
```

**记录的指标**：
```markdown
| 端点 | P50 | P95 | P99 | 错误率 |
|------|-----|-----|-----|--------|
| /api/chat/stream | ? | ? | ? | ? |
| /api/config | ? | ? | ? | ? |
| ...
```

**缺了什么**：没有真实的性能数据，只有脚本模板。

**什么时候做**：部署完成后 3-7 天内。

---

### 8. 性能基准数据存档 📁
**负责**: DevOps  
**预计时间**: 30 分钟  
**文件**: `docs/PERFORMANCE_BASELINE.md`

**文件模板**：
```markdown
# 性能基准报告

**测试时间**: 2026-09-XX  
**环境**: Production  
**部署版本**: v1.0.0

## 延迟指标

| 端点 | 方法 | P50 | P95 | P99 | 平均 |
|------|------|-----|-----|-----|------|
| /api/chat/stream | POST | ??? | ??? | ??? | ??? |
| ...

## 吞吐量

| 端点 | RPS | 错误率 |
|------|-----|--------|
| /api/chat/stream | ??? | ??? |
| ...

## 资源使用

| 指标 | 值 | 单位 |
|------|-----|------|
| CPU 利用率（峰值） | ??? | % |
| 内存使用（峰值） | ??? | MB |
| Redis 连接数 | ??? | conn |
| 数据库连接数 | ??? | conn |
```

**缺了什么**：没有真实的压测结果数据。

**什么时候做**：部署后 1 周内补充。

---

### 9. 运维团队培训 👥
**负责**: 技术负责人  
**预计时间**: 2 小时（讲座） + 1 小时（Q&A）

**培训内容**：
1. Grafana 仪表板怎么读（4 个服务健康状态、错误率、延迟）
2. 10 个关键告警的含义和处理方法
3. CircuitBreakerOpen / HighErrorRate5xx / 等常见告警怎么处理
4. 如何手动触发回滚（Kubernetes rollout undo / Nginx 权重调整）
5. 常见故障排查（Redis 断连 / DB 故障 / 熔断器卡住）

**缺了什么**：没有正式的"运维培训文档"（可选但推荐）。

**什么时候做**：部署前 1 天或部署后第 1 天。

---

## 🟢 Nice-to-have（可选，部署后补充）

### 10. Kubernetes 生产部署 YAML（如果用 K8s）
**负责**: DevOps  
**预计时间**: 3-4 小时  
**文件**: `k8s/deployment.yaml` 等

**需要的 YAML**：
- `Deployment` for lea-agent（含 readiness/liveness probe）
- `Service` 暴露端口
- `ConfigMap` for Prometheus / Grafana 配置
- `PersistentVolumeClaim` for 数据存储
- `HorizontalPodAutoscaler` 自动扩缩容

**缺了什么**：没有 K8s YAML，现在只有 Docker Compose。

**什么时候做**：可选，部署后 2-3 周补充。

---

### 11. CI/CD 集成脚本
**负责**: DevOps  
**预计时间**: 2 小时  
**文件**: `scripts/deploy.sh` 或 Jenkins/GitLab CI 配置

**脚本需要自动化**：
- 编译 4 个服务
- 运行单元测试
- 构建 Docker 镜像
- 推送到镜像仓库
- 执行 Helm/Kustomize 部署
- 运行 E2E 测试
- 收集性能基准

**缺了什么**：没有自动化部署脚本，现在都是手工命令。

**什么时候做**：可选，部署后 1-2 周补充。

---

### 12. 详细的故障恢复 SOP（Runbook）
**负责**: SRE  
**预计时间**: 2 小时  
**文件**: `docs/INCIDENT_RESPONSE.md`

**需要的 runbook**：
- 如果 CircuitBreakerOpen 持续 > 5 min，怎么办？
- 如果 5xx 错误率 > 10%，怎么办？
- 如果 Redis 连接全断，怎么办？
- 如果数据库连接池耗尽，怎么办？
- 如果灰度部署卡住，怎么办？

**缺了什么**：没有详细的故障应对流程（`QUICK_REFERENCE.md` 有简化版）。

**什么时候做**：可选，部署后补充。

---

## 📊 缺口优先级排序

### 第 1 优先级（必须做，否则无法上线）
```
1. Phase 1-5 验证完成 ← 部署前必须做
2. 生产环境参数配置 ← 部署前必须做
3. 数据库迁移执行 ← 部署前必须做
4. Prometheus + Grafana 生产部署 ← 部署前必须做
5. 灰度计划批准 ← 部署前必须做
```

**时间表**: 部署前 2-3 天完成所有 1-5

---

### 第 2 优先级（部署后 1 周内必须完成）
```
6. E2E 真链路测试执行 ← 部署完成后第 1 天
7. 性能基准测试 ← 部署完成后 3-7 天
8. 性能基准数据存档 ← 部署完成后 1 周内
9. 运维团队培训 ← 部署完成后第 1-2 天
```

**时间表**: 部署完成后 1 周内全部完成

---

### 第 3 优先级（部署后 2-3 周可选补充）
```
10. Kubernetes YAML ← 如果生产用 K8s
11. CI/CD 集成脚本 ← 便利后续部署
12. 详细 Runbook ← 长期运维支持
```

**时间表**: 部署稳定运行 2-3 周后补充

---

## 🎯 关键路径（Critical Path）

```
Day -2: Phase 1-5 验证 + 参数配置 + DB 迁移
Day -1: Prometheus 生产部署 + 灰度计划批准
Day 0: 执行灰度部署（5% → 10% → 25% → 100%）
Day 1: E2E 测试 + 运维培训
Day 2-7: 性能测试 + 基准存档 + 监控调优
```

---

## ✅ 检查清单

### 在提交部署申请前
- [ ] Phase 1-5 全部通过？
- [ ] .env.production 填完？
- [ ] 数据库迁移已执行？
- [ ] Prometheus + Grafana 已部署？
- [ ] 灰度计划已批准？
- [ ] 应急联系方式已确认？
- [ ] 值班人员已确认？

### 部署后 24 小时
- [ ] E2E 测试 7 个场景全部 PASS？
- [ ] Grafana 仪表板数据实时？
- [ ] 无 Critical 告警 > 1 小时？

### 部署后 1 周
- [ ] 性能基准数据已记录？
- [ ] 运维团队已培训？
- [ ] 故障演练已完成？

---

**总结**：从代码完成到真正能上线生产，还需要 7-10 天（含部署）。核心缺口不是代码或配置，而是**执行和验证**。

要需要我帮你补充第 10、11、12 项中的任何一项吗？
