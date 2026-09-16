# 🎯 行动计划 — 从现在到上线生产

**生成时间**: 2026-09-15  
**当前状态**: 代码 ✅ 配置 ✅ 文档 ✅ | 执行 ⏳

---

## 📊 当前状态快照

### ✅ 已完成（无需修改）
- **代码层**: P0 8 项 + P1 3 项 + Layer 1 完整 = 100% 就绪
- **配置层**: Prometheus + Grafana + Docker Compose = 100% 准备
- **文档层**: 12 份文档 + 完整指南 = 100% 完成
- **测试层**: E2E 脚本 7 个场景 + 验收脚本 = 100% 就绪

### ❌ 待执行（需要分工）
- **5 个 Critical 项**（部署前必须）
- **4 个 Important 项**（部署后 1 周内）
- **3 个 Nice-to-have 项**（部署后可选）

---

## 🚀 第 1 步：今天（30 分钟立即可做）

### 1.1 启动本地验证栈
```bash
# 进入项目目录
cd D:/gitlab/lea-platform

# 启动验证栈（包含 Prometheus + Grafana）
docker-compose -f infra/docker-compose.verify.yml up -d

# 等待服务启动
sleep 60

# 验证所有服务健康
curl http://localhost:9090/-/healthy     # Prometheus
curl http://localhost:3000/api/health     # Grafana
curl http://localhost:8200/health         # lea-agent
curl http://localhost:8500/health         # lea-config
```

**预期结果**: 7 个服务都返回 200 OK

✅ **通过条件**:
- Prometheus targets 能看到 4 个 UP
- Grafana 仪表板能访问（admin/admin）
- 仪表板显示实时数据（不是空白）

---

### 1.2 快速文档阅读（15 分钟）
按你的角色选择：
- **决策者/PM** → 打开 `FINAL_SUMMARY.md`
- **SRE/DevOps** → 打开 `QUICK_REFERENCE.md`
- **DBA** → 打开 `DEPLOYMENT_CHECKLIST.md` Phase 1
- **架构师** → 打开 `CURRENT_STATUS.md`
- **QA** → 打开 `scripts/expert_switch_e2e.py`

---

### 1.3 分工协调会（15 分钟）
**参与人**: 架构师 + PM + DBA + DevOps + SRE

**议题**:
1. 选择执行模式：严格 / 快速 / 激进？
2. 确认部署时间窗口（哪一天）
3. 分工确认（谁负责 Phase 1-5）
4. 应急联系方式确认

**输出**: 部署计划表 + 分工表

---

## 📅 第 2-3 天：部署前准备（关键路径）

### Phase 1: 基础设施验证（DBA + DevOps，30 min）
```bash
# DBA 检查：MySQL 迁移
mysql -h <prod-host> -u <user> -p lea << EOF
SHOW INDEX FROM chat_sessions WHERE Key_name = 'idx_sessions_user_tenant';
SHOW INDEX FROM chat_messages WHERE Key_name = 'idx_messages_created_expired';
DESC chat_sessions;
EOF

# DevOps 检查：Redis
redis-cli -h <host> PING
redis-cli -h <host> --latency 100    # 预期 < 10ms

# DevOps 检查：Nginx 限流
curl -I http://localhost/api/metrics
# 多次请求看是否返回 429
```

**通过条件**: ✅ 三个迁移都存在 ✅ Redis 延迟 < 10ms ✅ Nginx 限流有效

---

### Phase 2: 代码验证（自动化，30 min）
```bash
# 编译检查
cd packages/lea-agent && go build -o /tmp/lea-agent ./cmd/server/main.go
cd packages/lea-config && python -m py_compile src/lea_config/server.py
cd packages/lea-rag && python -m py_compile src/lea_rag/tasks.py
cd packages/lea-tools && python -m py_compile src/lea_tools/server.py

# 指标端点验证
for port in 8200 8500 8300 8400; do
  curl -s http://localhost:$port/metrics | head -5
done
```

**通过条件**: ✅ 4 个编译成功 ✅ 4 个 /metrics 都返回 Prometheus 格式

---

### Phase 3: Prometheus + 告警（SRE，60 min）
```bash
# 验证 Prometheus 配置
docker run -v $(pwd)/infra/prometheus:/etc/prometheus prom/prometheus:latest \
  prometheus --config.file=/etc/prometheus/prometheus.yml --dry-run

# 部署 Prometheus + Grafana 到 staging/production
# （Docker 或 Kubernetes，按你的基础设施选择）

# 验证采集
curl -s http://prometheus:9090/api/v1/targets | jq '.data.activeTargets | length'
# 预期: 4 或更多
```

**通过条件**: ✅ Prometheus targets 都是 UP ✅ 10 个告警规则已加载 ✅ Grafana 仪表板显示数据

---

### Phase 4: 灰度计划批准（架构师 + PM，60 min）
**需要正式确认**:
- [ ] 金丝雀百分比：5% → 10% → 25% → 100%
- [ ] 每阶段监控时间：5 min → 15 min → 15 min → 30 min
- [ ] 告警门槛（什么时候立即回滚）：5xx > 10% 或 CircuitBreakerOpen
- [ ] 部署时间窗口：避开业务高峰
- [ ] 回滚决策权：谁能拍板回滚

**输出**: 部署计划审批单（签名）

---

### Phase 5: 环境变量确认（DevOps，15 min）
**创建 `.env.production` 或配置到部署系统**:
```bash
REDIS_URL=redis://prod-redis:6379/0
DATABASE_URL=mysql://user:pass@prod-db:3306/lea
LEA_INTERNAL_TOKEN=<long-secret-same-for-3-services>
LEA_AGENT_SESSION_TTL=30m
CIRCUIT_BREAKER_MAX_FAILURES=5
CIRCUIT_BREAKER_TIMEOUT=60s
CELERY_BROKER_URL=redis://prod-redis:6379/1
CELERY_RESULT_BACKEND=redis://prod-redis:6379/2
LOG_LEVEL=info
```

**通过条件**: ✅ 所有关键参数已配置 ✅ REDIS_URL 不是 127.0.0.1 ✅ LEA_INTERNAL_TOKEN 三个服务相同

---

## 🎬 第 4 天：灰度部署（4 小时）

### 部署前最后检查（30 min）
```bash
# 确认所有 Phase 1-5 都通过
echo "✓ Phase 1: 基础设施验证"
echo "✓ Phase 2: 代码验证"
echo "✓ Phase 3: Prometheus + 告警"
echo "✓ Phase 4: 灰度计划批准"
echo "✓ Phase 5: 环境变量配置"

# 最后一次健康检查
curl -s http://prod:8200/health | jq .
curl -s http://prod:8500/health | jq .
```

---

### 灰度部署执行
```bash
# 步骤 1: 备份生产库（15 min）
mysqldump -h <prod> -u <user> -p lea > /backups/lea_$(date +%Y%m%d_%H%M%S).sql

# 步骤 2-5: 金丝雀部署（参考 DEPLOYMENT_GUIDE.md）
# 5% 部署 + 5 min 监控
# → 10% 部署 + 15 min 监控
# → 25% 部署 + 15 min 监控
# → 100% 部署 + 30 min 监控

# 监控命令（部署期间持续运行）
watch -n 5 'curl -s http://prometheus:9090/api/v1/alerts | jq '.data | length''
```

**成功条件**:
- ✅ 无任何 Critical 告警 > 1 小时
- ✅ 5xx 错误率保持 < 1%
- ✅ 所有 /metrics 端点返回 200 OK
- ✅ Grafana 仪表板数据实时更新

---

## 📊 第 5-7 天：部署后验收（可并行）

### E2E 真链路测试（QA，4 小时）
```bash
# 运行 7 个场景验收脚本
LEA_AGENT_URL=http://prod:8100 python scripts/expert_switch_e2e.py

# 预期输出
# ✅ Scenario 1: First message with agent_id → PASSED
# ✅ Scenario 2: Mid-switch → PASSED
# ✅ Scenario 3: Cancellation → PASSED
# ✅ Scenario 4: Multi-replica healing → PASSED
# ✅ Scenario 5: Expert team boundary → PASSED
# ✅ Scenario 6: Invalid agent fallback → PASSED
# ✅ Scenario 7: Orthogonal layers → PASSED
# All 7 scenarios PASSED ✅
```

---

### 性能基准测试（DevOps，3 小时）
```bash
# 安装 wrk（如未安装）
# brew install wrk（Mac）或 apt-get install wrk（Linux）

# 五个关键端点压测
wrk -t 4 -c 50 -d 300s http://prod:8200/api/chat/stream
wrk -t 2 -c 20 -d 300s http://prod:8500/api/config/<id>
wrk -t 2 -c 20 -d 300s http://prod:8300/api/search
wrk -t 2 -c 20 -d 300s http://prod:8400/api/execute
wrk -t 1 -c 10 -d 300s http://prod:8200/metrics

# 记录结果到 docs/PERFORMANCE_BASELINE.md
```

---

### 运维团队培训（技术负责人，3 小时）
**培训内容**:
1. Grafana 仪表板使用（如何读 4 个服务状态、错误率趋势）
2. 10 个关键告警（含义 + 处理流程）
3. 手动回滚流程（Kubernetes rollout undo / Nginx 权重调整）
4. 常见故障排查（Redis 断连、DB 故障、熔断器卡住）
5. 应急联系流程和值班规则

---

## ✅ 完整检查清单

### 部署前
```
☐ Phase 1-5 全部通过？
☐ .env.production 已配置？
☐ 数据库迁移已执行？
☐ Prometheus + Grafana 已部署？
☐ 灰度计划已批准？
☐ 应急联系方式已确认？
☐ 值班人员已确认？
```

### 部署中
```
☐ 5% 部署完成 + 5 min 监控 → 无告警
☐ 10% 部署完成 + 15 min 监控 → 无告警
☐ 25% 部署完成 + 15 min 监控 → 无告警
☐ 100% 部署完成 + 30 min 监控 → 无告警
```

### 部署后 24 小时
```
☐ E2E 测试 7 个场景全部 PASS？
☐ Grafana 仪表板数据实时？
☐ 无 Critical 告警 > 1 小时？
☐ 生产数据完整性验证？
```

### 部署后 1 周
```
☐ 性能基准数据已记录？
☐ 运维团队已培训？
☐ 故障演练已完成？
☐ 部署文档已发布？
```

---

## 🎯 关键里程碑

| 里程碑 | 时间 | 负责人 | 状态 |
|--------|------|--------|------|
| Phase 1-5 验证完成 | 部署前 2 天 | DBA/DevOps/SRE | ⏳ 待执行 |
| 灰度计划批准 | 部署前 1 天 | 架构师 + PM | ⏳ 待执行 |
| 灰度部署执行 | 第 4 天 | SRE | ⏳ 待执行 |
| E2E 测试通过 | 部署后第 1 天 | QA | ⏳ 待执行 |
| 性能基准完成 | 部署后 3-7 天 | DevOps | ⏳ 待执行 |
| 部署完全就绪 | 部署后 7 天 | 全队 | ⏳ 待执行 |

---

## 📞 决策点（现在需要确认）

### 必须确认的 3 项

**1. 执行模式**
```
[ ] 严格模式（3-4 天，推荐）
    - 完整的 Phase 1-5 验证
    - 部署前 2 天启动
    - 风险最低

[ ] 快速模式（2 天，可接受）
    - 简化的 Phase 1-5 验证
    - 部署前 1 天启动
    - 风险中等

[ ] 激进模式（1 天，不推荐）
    - 跳过部分验证
    - 风险高
```

**建议**: 严格模式（最稳妥）

---

**2. 部署时间窗口**
```
[ ] 本周末（9 月 XX 日）
[ ] 下周（9 月 XX 日）
[ ] 其他: ___________
```

**建议**: 避开周一早上和周五下午

---

**3. 是否需要补充**
```
[ ] Kubernetes 部署 YAML（如果生产用 K8s）
[ ] CI/CD 集成脚本（便利后续部署）
[ ] 详细故障恢复 SOP（长期运维支持）
```

---

## 🚀 立即行动（右现在）

### 这一刻就可以做：
1. **打开本地验证栈** (30 min)
   ```bash
   docker-compose -f infra/docker-compose.verify.yml up -d
   ```

2. **阅读部署文档** (45 min)
   - START_HERE.md（选择你的角色）
   - DEPLOYMENT_CHECKLIST.md（了解 Phase 1-5）
   - WHAT_IS_STILL_MISSING.md（完整缺口分析）

3. **组织分工协调会** (安排在明天)
   - 参与人：架构师、PM、DBA、DevOps、SRE
   - 议题：分工 + 时间表 + 部署模式选择

4. **DevOps 开始收集生产参数** (今天下午)
   - REDIS_URL
   - DATABASE_URL
   - 容量规划
   - 网络配置

---

## 📋 文档导航

| 场景 | 打开这个文件 |
|------|------------|
| "我不知道从哪开始" | `START_HERE.md` |
| "5 分钟了解全景" | `FINAL_SUMMARY.md` |
| "我需要执行清单" | `DEPLOYMENT_CHECKLIST.md` |
| "我需要快速命令" | `QUICK_REFERENCE.md` |
| "还缺什么？" | `WHAT_IS_STILL_MISSING.md` |
| "我是 SRE/DevOps" | `DEPLOYMENT_GUIDE.md` |
| "我想理解代码" | `PRODUCTION_READINESS_VERIFICATION.md` |

---

## 🎬 总结

**代码**: ✅ 100% 就绪（无需修改）  
**配置**: ✅ 100% 准备（可直接使用）  
**文档**: ✅ 100% 完成（可直接参考）  
**执行**: ⏳ 0% 就绪（需要团队执行）

**下一步**: 组织分工协调会，启动 Phase 1-5 验证。

---

**准备好了吗？🚀**

1. 启动本地验证栈
2. 打开 START_HERE.md 选择你的角色
3. 安排分工协调会议

**立即开始吧！**
