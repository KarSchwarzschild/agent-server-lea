# 上下文压缩 —— 现状核查与改造项

> 状态：待排期
> 关联：`agentloop/internal/compaction/`、`agentloop/internal/history/`、
> `packages/lea-agent/internal/persistence/engine_backend.go`

---

## 0. 结论先说

**引擎里有一整套压缩，而且顺序跟"应该怎么做"完全一致。它是关的。**

打开它是**一行配置**（`Config.MaxContextTokens`）。但打开之后会立刻撞上第二个问题，
那个问题是多节点特有的，需要动引擎：**摘要不落库**。

---

## 1. 引擎已有什么（实测）

三层，从便宜到贵，各管一段：

| 层 | 在哪 | 做什么 | 花模型调用吗 |
|---|---|---|---|
| **bound** | `history/bound.go` | 单个工具结果超限时在**写进历史的路上**截断 | 否 |
| **reduce** | `history/reduce.go` | 把**已经陈旧的**工具结果脱水（模型已经读过并往下走了的那些） | 否 |
| **compact** | `internal/compaction/` | 用模型把老轮次总结成一条摘要消息，近期若干轮保持原文 | 是 |

这正是"先把杂七杂八的工具执行过程删减，再用模型总结之前的对话"那个顺序，而且引擎把
它做成了**两道独立的门**（`session.go:790` 和 `:794`）：

> reduce 先跑，压不下去 compact 才跑。而且 compact 看到的是**已经 reduce 过**的历史，
> 两者用同一个 token 估算，所以第二道看到的是第一道的效果而不是过期数字。

几个值得知道的细节：

- `reduce` 只碰**陈旧**结果（`StaleAfter` 条消息之前的）。最新的结果一个字都不动 ——
  那正是模型当前在推理的东西，压它是拿正确性换 context
- `compact` 的摘要**增量折叠**：新摘要会把旧摘要的内容原样带上，不会滚着滚着丢事实
- 落盘的会话历史**始终是完整无损的**，压缩只作用于"发给模型的那份"

## 2. 为什么没生效

```go
// MaxContextTokens enables context compaction once a turn's input tokens
// approach this budget. 0 (default) disables compaction.
MaxContextTokens int
```

而 `maybeReduce` 和 `maybeCompact` **两个函数的第一行都是**：

```go
if s.maxContextTokens <= 0 { return ... }   // 直接返回
```

lea-agent 从来没设过这个字段（全仓搜 `MaxContextTokens` / `Compact` 零命中）。
所以**确定性脱水和模型摘要，两道全是关的**。

唯一一直生效的是 `MaxToolResultBytes`（默认非零，"the shield is always armed"）——
但它是**单个结果**的上限，防的是一次 `Read` 把整个上下文冲爆，不是上下文管理。
十次各自合规的工具调用照样能把上下文堆满，而那正是长会话的实际形状。

## 3. 多节点下的真问题：摘要不落库

打开配置之后会立刻撞上这个，所以要一起排。

```
Package compaction ... It operates only on in-memory working history;
the on-disk session store keeps the full, loss-less conversation.
```

而 `persistTurn` 只做一件事：`sessionStore.Append(ctx, messages)` —— **只追加原始消息**。
`commitHistory(final, ...)` 更新的是内存里那份。所以：

```
pod A  跑了 30 轮 → compact → 工作历史变成 [摘要, 近 4 轮]     ← 只在内存里
       落库的是   [30 轮的原始消息]                            ← 摘要没进去
              ↓ 负载均衡把下一条消息发给 pod B
pod B  LoadHistory 读回原始行 → 又超预算 → 又调一次模型总结
```

后果三条，按严重程度：

1. **每次 failover 多一次模型调用**，而这个调用是用户看不见的
2. **摘要每次都不一样** —— 同一段对话被两个副本各总结一次，措辞和取舍不同，
   于是"同一个会话在不同 pod 上记得的东西不一样"
3. 单副本跑久了也一样 —— 进程重启同理

这是**多节点特有的**：桌面场景一个进程从头跑到尾，内存里那份就是唯一的那份，
所以引擎这么设计对它的原生场景没有问题。我们的部署形态不同。

## 4. `maxLoadTurns = 20` 那一条

`engine_backend.go:25`，注释写得很清楚，判据是成本：

> 引擎自带的 jsonl/sqlite backend 全量加载，对桌面是对的（一个人、一个工作区、
> 文件就是对话）。这里同一个调用发生在**每个请求、跨一个 pod 集群、没有可继承的
> prompt cache**，所以整段历史每轮都按全价重发。实测这个平台上 base context 已经
> 占了输入成本的大头（PPT 任务约 70%），无上限加载会让长对话为了一个没人要求的
> failover 变得任意贵。

**所以 20 这个数不是随手定的，它是"在没有压缩的前提下"的止损。**

你觉得"载入 20 轮直接进上下文很怪"——对，但根因是**压缩关着**：压缩开着的话，
跨 pod 恢复本该载入的是「一条摘要 + 近几轮原文」，而不是 20 轮原始对话。

也就是说 §2 和 §4 是同一个问题的两面：因为没有压缩，只能用一个粗暴的轮数上限兜底；
而轮数上限本身又是个坏判据——**20 轮可能是 2 万 token，也可能是 40 万**，取决于这 20
轮里有没有人调过 `Read`。

---

## 5. 改造项

| # | 做什么 | 在哪改 | 依赖 |
|---|---|---|---|
| **1** | 设 `Config.MaxContextTokens`，按模型上下文窗口的比例给 | lea-agent | 无。**一行** |
| **2** | 摘要要能落库，恢复时优先读摘要而不是原始行 | **引擎** + lea-agent | 引擎的 store 目前是 append-only，需要一个"这条是摘要、覆盖它之前的"的表达 |
| **3** | 恢复的上限从**轮数**改成 **token 预算** | lea-agent | 依赖 1 和 2 |

**顺序不能反。** 只做 1：单副本内有效，跨 pod 每次重算摘要（比现在好，但多了看不见的
模型调用）。只做 3 不做 1：把粗判据换成细判据，但仍然没有压缩，长对话照样爆。

### 关于 #2 的形状

需要跟引擎作者对齐的是**存储契约**，不是压缩算法本身。可能的形状：

- store 支持一条"检查点"消息，`LoadHistory` 从最后一个检查点往后读
- 或者 lea-agent 自己在 `agent_messages` 里加一列标记摘要行，`LoadHistory` 自己处理

倾向前者——后者等于在 lea-agent 里重实现一遍压缩的语义（哪些行被摘要覆盖了），
而那个语义引擎已经有了。

---

## 6. 待定

1. **`MaxContextTokens` 给多少？** 模型窗口的比例（比如 60%）还是绝对值？
   引擎内部还有个 `compactionThresholdRatio` 会在接近预算时才触发，所以这个值不是
   "到了就压"，是"预算线"。要实测一次再定。

2. **摘要用哪个模型？** 压缩本身是个模型调用。用主模型准确但贵；用小模型便宜但摘要
   质量直接影响后续所有轮。`Summarize` 是注入的，两种都支持。

3. **多副本同时压缩同一个会话怎么办？** 两个副本各自触发 compact，各写一条摘要。
   引擎里有 `historyVersion` 的乐观锁保护内存那份，但跨副本没有。做 #2 的时候要一起想。
