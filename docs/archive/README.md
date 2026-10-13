# 归档：2026-09 "生产就绪"系列文档

这里是 2026-09-15/16 一波上线准备冲刺留下的 11 份文档，2026-10-13 从仓库根目录
移进来。**它们描述的是那个时点的状态和当时规划的部署方案，不是当前现状。**

当前实际在用的部署路径：

- 测试服（202.205.160.9）→ `docs/TEST_SERVER_DEPLOY.md`
- 生产 CCE/k8s → `infra/k8s/README.md`

## 为什么归档而不是删

两个原因。一是里面有一次性调研的成果（性能基准、灰度策略、监控部署步骤），结论
可能还有效，删了就得重做。二是归档比删除可回溯 —— `git log --follow` 还能查到。

## 为什么不能当现状读

- **端口全变了。**测试服现在是 8880（frontend-lab，并反代 /api）、8890
  （frontend-console）、8840（gateway）。这些文档里一个都没有。
- **`frontend-console` 完全不存在于这些文档中。**它是后来加的，现在是发布审批和
  审计的入口。
- **`frontend-user` / `frontend-admin` 在这些文档里还是在用的服务。**它们已经停用。
- **`PRODUCTION_READINESS_VERIFICATION.md` 把 "Celery 异步队列" 列为已验证项。**
  Celery 现在只存在于 `packages/lea-apps-legacy`（归档代码），实际异步执行链路是
  Temporal（`lea-agent-worker` + Task Queue）。照它排查异步问题会找错方向 ——
  这是这批文档里最有误导性的一处。
- 当时还没有发布审批/审计、会话级模型选择、用户长期记忆这些功能。

## 清单

状态快照类，内容互相重叠，基本只有历史价值：

- `COMPLETION_SUMMARY.md`
- `FINAL_SUMMARY.md`
- `PRODUCTION_READINESS_EXECUTIVE_SUMMARY.md`
- `PRODUCTION_READINESS_VERIFICATION.md` ← 含上面说的 Celery 误导
- `INDEX.md`
- `README_DEPLOYMENT.md`
- `START_HERE.md`

仍可能有可抢救内容，形式是"待办清单"但夹着调研结论：

- `WHAT_IS_STILL_MISSING.md` — 延迟/吞吐量基准数据，以及 Prometheus+Grafana
  生产部署的待办分解
- `ACTION_PLAN.md` — 上线前的阶段划分
- `CURRENT_STATUS.md` — 2026-09-15 的全景状态和优先级排序
- `DEPLOYMENT_CHECKLIST.md` — 5 个 Phase 的检查项，含灰度百分比策略
  （5% → 10% → 25% → 100%）

那些基准数字和灰度策略如果要继续用，建议核对后搬进 `docs/TEST_SERVER_DEPLOY.md`
或 `infra/k8s/README.md`，而不是继续从这里引用。
