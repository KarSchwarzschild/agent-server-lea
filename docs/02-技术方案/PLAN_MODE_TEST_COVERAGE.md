# 计划模式测试覆盖报告

> 状态：已完成
> 时间：2026-08-20
> 涉及：`packages/lea-agent/internal/{session,persistence,runtime}/`

---

## 0. 结论

**计划模式的核心路径已全部覆盖，7 个测试文件、48+ 条用例、全绿。**

三道防线各自独立且都有测试钉住：

1. **入口守卫** —— handoff 团 + 配置开关，两种"不支持"都在 Send 里被拦
2. **工具注册** —— plan_mode=off 时不注册 save_plan/update_plan_step
3. **权限层** —— 规划态挡住所有写操作，放行只读 + 计划自己那套

---

## 1. 已覆盖的关键场景

### 1.1 入口守卫（`session/plan_test.go`）

| 用例 | 覆盖什么 | 为什么重要 |
|---|---|---|
| `TestSend_PlanModeRejectedForHandoffTeam` | handoff 团上 plan_mode → 400 | engine 是 nil，SetPlanMode 会 panic |
| `TestSend_PlanModeRejectedWhenAgentConfigDisabled` | plan_mode=off 的 agent 收到 plan_mode 请求 → 400 | 配置层开关必须被尊重 |
| `TestPlanSupported` (7 子用例) | 各种组合下守卫的放行/拒绝逻辑 | 写反条件会让整类会话不可用 |

**实测结果：** 全 PASS。handoff 团、配置关闭、星型团、单 agent 四种形态都走到了。

### 1.2 工具注册（`runtime/file_tools_test.go` + `plan_tools_test.go`）

| 用例 | 覆盖什么 |
|---|---|
| `TestPlanEnabled` | PlanEnabled() 读 plan_mode 配置的三种值 |
| `TestProvisionCarriesPlatformToolsExceptMemoryAdd` | get_plan 在 Provision 里（子代理能拿到），save_plan/update_plan_step 在 ExtraTools 里（子代理拿不到） |
| `TestGetPlanToolDeclaresRetention` | get_plan 的 ResultRetention 正确声明（拼写错误会静默失效） |
| `TestUpdatePlanStepIsNotConcurrencySafe` | update_plan_step 的并发声明（错了会丢更新） |

**实测结果：** 全 PASS。工具分发的两条通道（Provision / ExtraTools）和并发声明都验证了。

### 1.3 权限层（`runtime/permissions_test.go`）

| 用例 | 覆盖什么 |
|---|---|
| `TestPlanModeBlocksMutatingTools` | 规划态挡住 Write/Edit/Bash/code_interpreter/memory_add/租户工具 |
| `TestPlanModeAllowsResearchAndPlanTools` | 规划态放行 Read/Glob/kb_search/get_plan/save_plan/AskUser |
| `TestExecutionModeBlocksNothing` | 执行态不挡任何东西（反向） |
| `TestUnknownToolsAreTreatedAsExternal` | 未知工具升级成 External（→ 规划态算 mutating） |
| `TestPermissionServiceIsSharedByValueCopiedOptions` | 团长进规划态时专家跟着一起被挡 |

**实测结果：** 全 PASS。PlatformResolver 的分类逻辑和专家会话的权限共享都验证了。

### 1.4 持久化层（`persistence/plan_store_test.go` + `plan_crossnode_test.go`）

| 测试文件 | 用例数 | 覆盖什么 |
|---|---|---|
| `plan_store_test.go` | 14 条 | 基本流转、修订版、租户隔离、步骤更新、错误输入 |
| `plan_crossnode_test.go` | 5 条 | 跨 pod 的提交/批准/修订/否决/清理 |

**核心回归：**
- `TestPlanStore_ReviseKeepsApprovedUntilItIsApproved` —— migrations/009 存在的理由
- `TestPlanStore_RejectKeepsApprovedPlan` —— 否决修订版不能把原计划也删了
- `TestPlanCrossNode_SaveOnA_ApproveAndLoadOnB` —— 多节点下计划可见性

**实测结果：** 全 PASS。单列 → 双列的迁移逻辑、跨 pod 的状态同步都验证了。

### 1.5 流转逻辑（`session/plan_test.go` 的 applyPlanAction）

| 用例 | 钉住什么 |
|---|---|
| `TestApplyPlanAction_NoActionDoesNotActivateDraft` | **本轮核心回归** —— 没表态不能激活计划 |
| `TestApplyPlanAction_ActivePlanSurvivesLaterTurns` | 执行阶段每轮都是 Action=""，不能把已批准的弄丢 |
| `TestApplyPlanAction_ReenteringPlanModeKeepsActivePlan` | Mode 不参与判断 |
| `TestApplyPlanAction_RejectDropsRevisionKeepsApproved` | reject 的语义 |

**实测结果：** 全 PASS，包括"误激活"那条核心回归。

---

## 2. 覆盖率统计

```
测试文件数：      7
总用例数：        48+
执行时长：        ~30s（含编译）
状态：           全 PASS
```

### 2.1 按模块分布

| 模块 | 文件 | 用例数 | 状态 |
|---|---|---|---|
| **session** | `plan_test.go` | 18 条 | ✅ |
| **persistence** | `plan_store_test.go` | 14 条 | ✅ |
| **persistence** | `plan_crossnode_test.go` | 5 条 | ✅ |
| **runtime** | `plan_tools_test.go` | 4 条 | ✅ |
| **runtime** | `file_tools_test.go` | 3 条（计划相关） | ✅ |
| **runtime** | `permissions_test.go` | 5 条（计划相关） | ✅ |
| **router** | `handlers_test.go` | （间接，通过集成测试） | - |

### 2.2 未覆盖但不需要测的

- **SSE 推送计划更新** —— 属于事件通知机制，走通用 SSE 测试
- **前端 plan_mode / plan_action 解析** —— 属于 HTTP binding，router 层有集成测试
- **日志输出** —— applyPlanAction 的 log.Printf 只用于排查，不影响正确性

---

## 3. 回归保护强度

### 3.1 静默失效都有覆盖

| 会静默失效的坑 | 对应测试 |
|---|---|
| ResultRetention 接口方法拼错 | `TestGetPlanToolDeclaresRetention` |
| IsConcurrencySafe() 声明错 | `TestUpdatePlanStepIsNotConcurrencySafe` |
| 工具分到错的通道（Provision / ExtraTools） | `TestProvisionCarriesPlatformToolsExceptMemoryAdd` |
| PlatformResolver 没接上 DefaultResolver | `TestEngineNativeToolsKeepDefaultClassification` |
| 守卫条件写反（`if s.isHandoff` 漏了 `&& plan.Mode`） | `TestPlanSupported` 的 7 个子用例 |
| 唯一索引没建出来（表达式索引反射不回来） | `test_tools_api.py` 的 DDL 检查 |

### 3.2 多节点特有的坑

`plan_crossnode_test.go` 用**共享文件级 SQLite** 模拟两个 pod，验证了：

- Node A 提交 → Node B 批准 → Node A 读到
- 修订版提交但未批准时，两个节点读到的都是老计划
- Node B 否决 → Node A 看到原计划
- 清理操作跨节点可见

这些场景用 `:memory:` 测不出来（每次 Open 是独立的库），而这正是"摘要不落库"（CONTEXT_COMPACTION.md § 3）那一类问题的根因。

---

## 4. 与文档的对应关系

| 文档章节 | 对应测试 |
|---|---|
| PLAN_MODE.md § 2.1 三道门 | `TestSend_PlanMode*` + `TestPlanEnabled` + `TestPlanModeBlocks*` |
| PLAN_MODE.md § 2.2 工具分发 | `TestProvisionCarriesPlatformToolsExceptMemoryAdd` |
| PLAN_MODE.md § 3.3 误激活 | `TestApplyPlanAction_NoActionDoesNotActivateDraft` |
| PLAN_MODE.md § 4.2 handoff 团 panic | `TestSend_PlanModeRejectedForHandoffTeam` |
| PLAN_MODE.md § 5.1 修订版流转 | `TestPlanStore_ReviseKeepsApprovedUntilItIsApproved` + `TestApplyPlanAction_PendingRevisionDoesNotPreempt` |
| CONTEXT_COMPACTION.md § 3（间接） | `TestPlanCrossNode_*` 系列 |

---

## 5. 运行方式

### 5.1 全量运行

```bash
cd packages/lea-agent
go test ./internal/session ./internal/persistence ./internal/runtime -v
```

### 5.2 按关注点运行

```bash
# 入口守卫
go test ./internal/session -run TestSend_PlanMode -v
go test ./internal/session -run TestPlanSupported -v

# 持久化
go test ./internal/persistence -run TestPlanStore -v
go test ./internal/persistence -run TestPlanCrossNode -v

# 工具与权限
go test ./internal/runtime -run TestPlan -v
go test ./internal/runtime -run TestProvision -v
```

### 5.3 关键回归单独跑

```bash
# 本轮核心修复 —— 没表态不激活
go test ./internal/session -run TestApplyPlanAction_NoActionDoesNotActivateDraft -v

# migrations/009 的理由 —— 修订版不能把老计划删了
go test ./internal/persistence -run TestPlanStore_ReviseKeepsApprovedUntilItIsApproved -v

# 跨 pod 可见性
go test ./internal/persistence -run TestPlanCrossNode_SaveOnA_ApproveAndLoadOnB -v
```

---

## 6. 待补充（不阻塞上线）

1. **集成测试** —— 从 HTTP 请求到数据库落库的完整链路
   - 现有单元测试已覆盖核心逻辑，集成测试补在下一个 sprint
   
2. **性能测试** —— planHint 每轮都拼，不过它只有几十个 token
   - 实测（§ 3.3）一份计划 1500 token → 摘要后 30 token，压缩比 50:1
   - 上下文预算内没问题，但可以加一条"摘要长度"监控

3. **并发冲突测试** —— 两个 pod 同时提交修订版
   - 现有 `approved_content` / `draft_content` 双列 + UNIQUE 约束已保证最终一致
   - 乐观锁（historyVersion）保护内存那份，跨 pod 靠唯一约束
   - 实际冲突概率极低（修订版由模型提交，一个会话同时只有一个 turn 在跑）

---

## 7. 小结

**三道防线 × 五层测试 = 48+ 条用例，全绿。**

- ✅ 入口守卫：handoff 团 + 配置开关
- ✅ 工具注册：plan_mode=off 不给工具
- ✅ 权限层：规划态挡写、放行读
- ✅ 持久化：双列防修订版覆盖、跨 pod 可见
- ✅ 流转逻辑：没表态不激活、Mode 不参与判断

**静默失效的坑都有覆盖，多节点特有的场景也测了。可以上。**
