# 专家团第二形态：handoff（接力式）

> 状态：**已落地并真机验证**（引擎 `agentloop@c47869d`、lea-agent `c84ee6e`）
> 前置：`AGENT_TEAM_ENGINE_PROPOSAL.md`（引擎侧 `ask_expert`）、`AGENT_TEAM_DEV_GUIDE.md`（星型怎么接）
> 关联任务：#251（本文档）、#247（`strategy` / `role` 字段接上 —— 顺带做完了）
>
> **本文档的估算部分已被实测替换。** 原先 §二 用的是从星型数据推的估算，实测结果方向
> 一致但数字差得远（估"省 2.8%"，实测"贵 18%"）—— 保留了推导过程，因为错在哪比结论
> 本身更值得记。见 §二末的对照。

---

## 一、为什么要第二形态

现在只有一种拓扑：**星型**。团长调 `ask_expert`，专家跑完把报告作为 tool result 交回，团长再决定下一个。专家之间没有任何通路 —— `ask_expert` 的 description 里明说：

> experts do not share a conversation, so include any findings from earlier consultations that this one depends on

所以「B 要知道 A 的结论」只能靠团长把 A 的报告读进来、理解、重写进 B 的 prompt。Anthropic 把这个叫 **game of telephone**，并且他们自己的做法是让子代理写文件、只传轻量引用，绕开协调者。

handoff 形态要的是：**专家自己指定下一棒**，团长不再逐轮转述。

---

## 二、先把成本讲清楚：这不是省钱方案

`.local/t6/` 的两轮真机数据（`msgs_t3.json` / `msgs_t4.json`），按层拆开：

| | t3 轮 | t4 轮 |
|---|---|---|
| **团长自己** | 23,210（**3.8%**） | 16,250（**3.9%**） |
| 专家会话（3 个） | 39,611（6.4%） | 68,526（16.3%） |
| 子代理（skill runner） | 552,859（**89.8%**） | 336,806（**79.9%**） |
| 全链路 | 615,680 | 421,582 |

t4 那轮的形状（嵌套关系按 `sse_t4.log` 的 `parentToolUseID` 核实，**不是拍平的**）：

```
团长      16,250   3 轮   peak 5,881
 ├─ ask_expert 内部沟通专家   25,865  peak  6,206   5 轮   （没派子代理）
 ├─ ask_expert 文档处理专家   23,265  peak  5,154   5 轮
 │   └─ Task general-purpose  58,721  peak 10,922   8 轮
 └─ ask_expert 文档处理专家   19,396  peak  5,535   4 轮
     └─ Task general-purpose 278,085  peak 21,264  17 轮   ← 单项占 66%
```

**每个 `Task` 都挂在一个 `ask_expert` 下，没有一个直接挂团长**（`started Task id=859427ba parent=9fbbab2b`，
而 `9fbbab2b` 就是文档处理专家那次 `ask_expert`）。这证实三层设计兑现了：

那 278,085 不是「专家团花的钱」，是**专家为了不让 skill 执行过程进自己上下文而主动付的隔离成本**。
不派子代理的话这些内容会落在专家会话里，专家的 peak 就不是 5,535 而是 21,264+。三层的固定开销
（团长 3.9% + 专家 16.3%）买到的正是这个隔离。

> ⚠️ **写这段时**`chat_messages.extra` 里的 `usage.agents[]` 是**拍平的五条，没有 parent** —— 只有 SSE
> 实时事件带 `parentToolUseID`。所以从落库数据看不出成本归属，本文档第一版的拓扑图就是这么画错的
> （把 Task 画成团长的直接子节点）。
>
> **已修**（`agentloop@e3e00aa` 事件桥 + `lea-agent` collector）：`agent_usage` 的 `toolCallId` 现在
> 恒等于派生它的那次调用，`parentId` 只在多层委派时非空。落库数据能还原三层链了。
>
> 这条缺陷后来**又以另一种形式出现了一次** —— handoff 的 leg 给事件桥传了空 `callID`，于是子代理的
> `parentId` 又空了（run3 抓到，467k 无法归属）。同一个坑换个门进来，见 §八 实测那栏。

**团长只占 3.9%。** handoff 优化的就是这一层，而且不是全省 —— 总得有人接用户消息、决定第一棒（见 §四）。加上头尾专家之后：

```
现在:  团长 16,250（3 轮 × ~5,400）
handoff: 头专家 ~1,500 + 尾专家 ~3,000 = ~4,500
```

省 **~2.8%**，且是估算。

那 278,085 是跑 `pptx-generator` 的 skill runner，上一轮已拆明：起始底座 11,452、每轮增长 613，`11,452 × 17 = 194,682 = 70%` 是底座重传。**省钱要动那里（#232 / #248），不在本文档范围。**

> 结论：handoff 的价值是**编排能力**（专家自主接力 + 专职验收），不是成本。任何把它当省钱项汇报的说法都不成立。

### ⚠️ 实测推翻了上面的数字（方向对，量级错）

同一任务同一提示词（一份 250 字 md 文档），team 1 走星型 vs team 2 走 handoff：

| | handoff | 星型 | 差 |
|---|---|---|---|
| 合计输入 | 37,583 | 31,793 | **+18%** |
| 入口专家 / 团长 | 3,035 | 9,159 | **−67%** |
| 工作专家 | 20,432 | 22,634 | −10% |
| **验收专家** | **14,116** | — | **纯增量** |

**handoff 贵 18%，不是省 2.8%。** 但错的不是"不省钱"这个结论，是**归因**：

- 上面估的是"团长那 3.9% 里省一部分"。实测入口专家确实更省，而且远超预期（−67%，
  6,124 tok）—— 因为它拿掉了干活的工具，工具 schema 不进 prompt 了
- 真正的增量是**验收专家那一整条 leg**（14,116），星型压根没有这道关。省下的 6,124 +
  worker 少的 2,202 全被它吃掉，净多付 5,790

所以准确说法是：**接力本身更省，贵的是验收。**

验收那 14k 是**固定成本**，占比随任务规模骤降：

| 任务 | 全链 | 验收占比 |
|---|---|---|
| 250 字 md | 37,583 | **37%** |
| md + 3 页 PPT | 555,538 | **2.5%** |

**轻任务用 handoff 不划算，重任务验收几乎免费。** 两个样本点，别当精确模型用。

另外那 555,538 里子代理占 **84%**（467,543），三条 leg 只占 16% —— handoff 压根不改
第三层，所以"要省钱得动 skill runner"（#232 / #248）这条判断实测成立。

同一任务同一提示词，两轮子代理开销 294k vs 467k（**方差 59%**），也是 `pptx-generator`
那条流程的问题，与拓扑无关。

---

## 三、拓扑对照

```
星型（现在）—— 三层，Task 挂在专家下面
用户 → 团长会话.RunTurn
         ├─ask_expert→ 专家A 会话 ──Task──> 子代理   ┐ 报告回到团长
         ├─ask_expert→ 专家B 会话 ──Task──> 子代理   ┤ 团长每次都要读+重写下一个 brief
         └─ask_expert→ 专家C 会话                    ┘

handoff（本文档）—— 第三层不变，只改第一层的走法
用户 → 入口专家 ──next──> 专家A ──next──> 专家B ──next──> 验收专家 → 结束
       （选人）              └─Task→子代理   └─Task→子代理   （判断真做完了没）
```

**handoff 只改最上面那一层。** 专家仍然是独立完整会话、仍然持有 `Task`、仍然把 skill 执行下沉给子代理 ——
第二三层完全不动。这也是为什么它只能影响那 3.9%：下面两层的成本结构与拓扑无关。

**用户视角两者相同**：一次输入 = 一次对话。星型现在就是这样 —— 内部建了好几个专家会话，但 SSE 是一个 `start`…`done`，库里一条 user + 一条 assistant。handoff 必须保持这个语义，链上跑了几个专家是实现细节。

### 与 B（专家互叫）的区别

三者常被混为一谈，区别是**控制权回不回来**：

| | 星型 | B 嵌套 | handoff |
|---|---|---|---|
| A 调完 B 之后 | — | A 继续跑 | **A 已退出** |
| 数据结构 | 一层调用 | 调用栈 | 状态机 |
| 谁产出最终回答 | 团长 | 团长 | 验收专家 |
| 每层底座 | 1 份 | **N 份** | 1 份 |

B 是把现在的模式再叠一层（每层再付一份完整 prompt + 工具 schema + skill 目录）。handoff 是换机制，深度不增加。**B 不做** —— 它在 4% 里面，代价却是每层一份底座。

---

## 四、头尾专家：解决 handoff 最大的漏洞

纯 handoff 靠每个专家自己声明「没有下一棒了」来结束。这是最容易出错的地方：专家做了一半就宣布完事，**没有任何人反驳**。

固定两个角色：

| role | 职责 | 为什么它便宜/必要 |
|---|---|---|
| `entry` | 读用户请求，选第一棒 | 不需要 skill、不需要工具 → 底座压到 ~1,000 量级 |
| `worker` | 干活，指定下一棒 | 就是现在的专家 |
| `exit` | 判断任务真的完成了 | 现在**完全没有**这道关 |

Anthropic 的做法是同一个思路：报告出去之前有一个专职 `CitationAgent` 逐条核对引用，不是让研究 agent 自己说「我引对了」。

### 验收专家凭什么判断

它没看过程 —— handoff 模式下专家之间不共享历史。证据只有三个来源：

1. **原始用户请求**（编排器传给它）
2. **workspace 里的产物** —— 它有 Read，能直接看文件
3. 中间专家的报告链

第 2 条是关键，而它把 `.team/findings.jsonl`（`AGENT_TEAM_DEV_GUIDE.md:301` 的 T7）从「锦上添花」变成**必需**：那就是验收专家的审计轨迹。

> 这条反过来给共享结论板找到了真正的用处。原先它只是「让 B 别重复 A 的劳动」，现在它是验收的输入。

---

## 五、库里的字段刚好够，零迁移

```python
# lea-config/models/team.py
strategy = Column(String(50), default="round_robin")   # :16
role     = Column(String(50), default="worker")        # :30
```

- `strategy = "handoff"` 选形态；`"autonomous"` 保持现在的星型
- `role = "entry" / "worker" / "exit"` —— 自由字符串，不用改 schema

### `role` 现在是加载了但没人消费

全链路核实过：

| 位置 | 状态 |
|---|---|
| `internal.py:94` `_row_to_dict(m)` | ✅ 带 role 返回 |
| `config_loader.go:39` `TeamMemberDef.Role` | ✅ 接到了 |
| `expert_assembly.go:83-87` | ❌ **只读 `m.Name`，Role 拿到就扔** |

`grep -n "m.Role"` 在整个 lea-agent 里零命中。

同理 `strategy`：`agent_session.go:122` 只拿它打一行日志，`decomposer_prompt` / `synthesizer_prompt` / `max_subtasks` 全部未消费 —— 所以 `pipeline` 和 `autonomous` 目前**行为完全一样**。

> 做 handoff 顺带就把 #247 做了。

**✅ 已接上**（`expert_assembly.go` + `agent_session.go`）：

```
agent_teams.strategy      → team.ParseMode  → engine.Config.TeamMode
agent_team_members.role   → runtime.EngineRole → team.Expert.Role
agents.max_turns（成员的） → team.Expert.MaxIterations   ← 这条是真机踩出来的
```

三处兜底都记了日志而不是静默：`strategy` 认不出来（库里现有的团都是默认 `round_robin`
或 `autonomous`）退回星型 —— 星型是这个字段有值以来一直的实际行为，所以对已有的团不是
变更；`role` 认不出来按 `worker`；两者在 handoff 下都会变成 `ValidateChain` 的"缺
entry/exit"，那条报错会列出全部专家名。

`decomposer_prompt` / `synthesizer_prompt` / `max_subtasks` **仍未消费** —— 它们属于
"团长拆解+汇总"那套（星型的进阶用法），handoff 用不到。#247 剩这一半。

---

## 六、实现路线：两个选项

### 路线 1 · lea-agent 侧自建编排器（不改引擎）

引擎已经给了模式：`expertRunner.Run` 就是「`Build()` 一个会话 → `RunTurn()` 一轮 → `Close()`」。lea-agent 可以在 `Send` 里自己写这个循环，不经过团长的工具循环。

**致命问题**：必须复刻 `expert_runner.go:93-158` 的 `derive` 字段表 —— 十几个字段（`Experts=nil`、`PersistSession=false`、`ToolMetadata` 打标、`SystemPrompt=renderExpertPersona`、`ToolEvents` 改道…）。而引擎在注释里明说其中一条不能交给宿主：

> Experts is forced to nil. This is the recursion guard, and it is placed here — in the engine, on config the host cannot reach — **precisely so that "an expert must not get ask_expert" is not something a host can forget**.

自建就把一条**结构性保证**降级成「我记得写」，而且两份 derive 要永久保持同步。

**不取这条。**

### 路线 2 · 引擎侧加执行框架（取这条）

理由除了上面的护栏，还有：桌面客户端同样需要这两种形态，在 lea-agent 原型必然要搬一次。

#### 关键发现：不需要动 `runTurn` 核心循环

`repl/session.go:655-664` 已经有「工具让本轮提前结束」的出口：

```go
if stop {
    // The user denied a tool and asked to stop. Finalize and return without
    // looping back to the model, so control returns to the user.
    result.Stopped = true
    ...
    return result, nil
}
```

现在只有权限拒绝会置位。handoff 要的是同一条路 —— **给已有出口加第二个理由**，不是新增控制流。「一轮以模型文本结束」这个假设 `Stopped` 已经打破了。

#### 改动清单

| | 内容 | 量级 |
|---|---|---|
| `team.HandoffTool` | 新工具 `handoff_to`；`Run` 返回带 `Metadata` 标记的 result | 一个新文件 |
| 信号传递 | 复用 `tool.Result.Metadata`，**不加字段** | 小 |
| `runTurn` 认信号 | `executeToolUses` 的 `stop` 多一个来源 | 十几行 |
| `turn.Result.Handoff` | 把 expert + brief 带回宿主 | 小 |
| 导出建会话 | `derive` 那张表变公开 API | 中 |
| `Config.TeamMode` | star / handoff，决定注册哪个工具 | 小 |

**实际改动**（引擎 +4257/-178，37 文件，其中测试占大半）跟这张表基本吻合，多出来的：

| 内容 | 为什么表里没有 |
|---|---|
| `team.Role` + `ValidateChain` | 头尾专家是 §四 提的，写这张表时还没并进来 |
| `Config.MaxHandoffs` | 星型有天然上限（团长的 `MaxIterations`），链没有 —— **每次转移是新会话新预算**，两个 worker 互相甩锅可以各自都待在预算内还一直跑 |
| `Expert.MaxIterations` | 真机踩出来的（专家配 30 却跑团长的 12） |
| entry 的 `DisabledTools` | 真机踩出来的（提示词禁令输给工具集手段） |
| `handoff_to` 拒绝 self | 真机踩出来的 |
| `permissions/resolver.go` | 顺带修的**已发布星型的 bug**：`ask_expert` 没分类，除 flight 外全模式硬拒 |
| `chain_hop` SSE | 链是平的，前端没有父子关系可推断分棒 |

#### 谁写那个 for 循环：**宿主**

引擎里已有先例 —— `expertRunner.Run` 就是宿主之外的一层在建会话/跑一轮/关掉。而 `Session` 的定义是「一个 `repl.Session` + 它的资源」（`engine.go:183`），handoff 链天然是 N 个会话，硬塞进一个 `Session` 要么改它的定义，要么在里面藏一个会话池。

```go
// turn.Result 加一个字段
Handoff *HandoffRequest   // 非 nil = 本轮转交，带 expert + brief

// 宿主循环
for {
    r, err := sess.RunTurn(ctx, msg)
    if r.Handoff == nil { break }
    sess.Close(ctx)
    sess, _ = engine.BuildExpertSession(cfg, opts, r.Handoff.Expert)
    msg = r.Handoff.Brief
}
```

引擎负责**建会话**（护栏留在引擎里），宿主负责**跳**。

**为什么不让引擎内部换活跃会话**：`PersistSession` / `Resume` / `Continue`（`config.go:53-55`）全是单会话语义。handoff 链写成一条 transcript 还是 N 条，是产品决策 —— 桌面可以选一条（用户看到连续对话），服务端选 N 条也行。引擎不该替两边定这件事。

#### ⚠️ 最终实现改成了**引擎侧驱动**（`engine.RunChain`）

上面那段推理有个漏洞：它只看了"谁建会话"，没看**编排规则本身归谁**。而 §七.1 定下来的
那些规则 —— 验收专家何时必经、断链兜给谁、封顶怎么处理、哪种错能继续 —— 全是设计决策，
不是胶水。宿主自己写 for 循环，就是每个宿主重写一遍这些规则，两份实现会朝**最后被调试
的那个宿主**的方向静默分叉。这跟否掉路线 1 是同一条理由（那边是两份 `derive` 要同步，
这边是两份编排规则）。

所以：

```go
res, err := engine.RunChain(ctx, cfg, opts, userText)
// res.Final / res.Legs / res.Usages / res.Stopped / res.DivertedToExit
```

`turn.Result.Handoff` 仍然是公开的，想自己驱动 leg 的宿主（比如桌面端要长驻会话、追问
直接给上一棒）可以走底层。**默认路径在引擎里，逃生口留着** —— `RunChain` 自己就是用那个
逃生口写的，所以两条路行为一致，不是两份实现。

`derive` 也因此没有变成公开 API：它抽成了内部的 `deriveExpertBase`，被星型和 handoff
共用。这比导出它更好 —— 加一个字段只要改一处，而导出之后宿主拷一份就又有两份要同步了。

---

## 七、待决

### 1 · 链断了怎么办（必须定）

专家不填 `next`、或填了不存在的专家名。三个选项：

- 兜给 `exit` 专家，让它判断能不能收尾
- 报错终止，SSE 发 error
- 退回 `entry` 重新选

**倾向第一个** —— 已经有验收专家了，让它兼任兜底最自然，且不引入新角色。

**✅ 已定：取第一个（断链），但"预算耗尽"单独走结束链。**

这两种情况一开始被当成一回事（都是"产出在 workspace、没人接手"），实测证明差别在
**验收专家知不知道自己在看什么**：

| | 忘记交棒 | 预算耗尽 |
|---|---|---|
| 实际状态 | 大概做完了 | 大概**没**做完 |
| 验收专家看到的 | 文件 | 同样只有文件 —— 看不见"这轮被切断了" |
| 后果 | 核对是合适的判断 | 把半成品当完整产出报告 |

run1 就是后者：三页 slide 源码写了但第三页没编译完。按兜底跑下去，验收专家会说
"任务完成"，用户拿到缺东西的交付物。

主流产品（ChatGPT / Copilot Workspace / Cursor / Claude Code）撞上限都是**停下、报告
状态、等用户决定**，不自动进下一阶段 —— 耗尽通常意味着没做完，把不完整产出交给下游
会让下游基于错误前提判断。所以预算耗尽 → `RunChain` 返错，产物仍在 workspace 和落库
的工具事件里，用户看得到做到哪一步。

### 2 · 用户追问谁接

handoff 模式下最后说话的是 `exit` 专家，它不认识用户、也没有对话历史。选项：

- 下一轮从 `entry` 重新开始（简单，但丢上下文）
- 记住 active expert，追问直接给它（要存状态；我们 session 绑单 pod，进程内就够，不需要 Redis）

服务端每轮重建会话，倾向第一个。桌面长驻对话可能要第二个 —— 这正是「宿主驱动」的价值：两边各自决定。

**✅ 已定：每轮从 `entry` 重新开始。** 实现上是自然结果 —— `RunChain` 每次调用都从
`entry` 起，没有"上一轮停在哪"的状态。桌面端要第二种就自己驱动 leg（`turn.Result.Handoff`
是公开的），引擎不替两边定这件事。**追问行为未真机验过。**

### 3 · `next` 协议的形状

结构化标记（`<next expert="..." brief="..."/>`）还是工具调用（`handoff_to`）？

**倾向工具调用**：schema 能约束、填错名字引擎能报可纠正的 `is_error`（照 `team/tool.go:160` 的 `unknown expert` 那条），而标记要自己写解析和容错。

**✅ 已定：工具调用。** 而且实测证明"能报可纠正的 is_error"这条价值比预想的大 ——
`handoff_to` 除了拒绝不存在的名字，还要拒绝**转交给自己**：验收专家核对完文件、结论是
"任务已完成"，然后还是调了 `handoff_to` 点自己，白烧 14k。目录里本来就排除了持有者
自己，不够 —— 那个名字它从人设里就知道（"你是 X 专家"）。**省掉一个选项只能挡住从
列表里学选择的模型，工具本身必须拒。** 结构化标记方案里这道校验得自己写。

### 4 · 前端

SSE 事件复用 `ask_expert` 那套（`agentName` 带专家名），前端不用改。但 handoff 链的可视化是**平铺**而不是嵌套 —— 现在 collector 把子会话事件按 `ParentToolUseID` 嵌套（`collector.go:85`），handoff 没有父子关系。**未验证前端怎么渲染。**

**⚠️ 仍未解决，但形状清楚了。** 新增了 `chain_hop` SSE 事件（每棒一条：专家名 / 角色 /
交给谁 / 用量 / 耗时），前端不处理也不会坏，只是看不见分棒。归属这块反而**有**父子关系
了 —— leg 用合成 id（`leg_N_专家名`），子代理的 `parentId` 指向派它的那条 leg，所以
成本归属能还原（实测 467k 的子代理正确指向 `leg_2_文档处理专家`）。但 leg 自己是平的
（`parentId` 为空 = 挂在 turn 上），前端要按 `chain_hop` 自己分段。属于 #193 前端重写
范围。

---

## 八、验证清单

必须真机端到端（项目规则：SB-2 / H5.G 都踩过）。

| # | 验什么 | 怎么验 |
|---|---|---|
| 1 | 编译 + 单测 | 两仓库 `go build ./... && go vet ./... && go test ./... -count=1` |
| 2 | 递归护栏仍在 | handoff 会话没有 `ask_expert` 也没有 `handoff_to` 的 schema |
| 3 | 一次对话语义 | SSE 一个 `start`…`done`；库里一条 user + 一条 assistant |
| 4 | workspace 共享 | 链上每个专家都用团长的 sessionID，产物在同一目录 |
| 5 | 沙箱只有一个 | 跑完确认 Cube 里一个实例，不是每棒一个 |
| 6 | `role` 生效 | `entry` 先跑、`exit` 最后跑，日志能看出顺序 |
| 7 | 链断兜底 | 手动配一个填错 `next` 的专家 → 按 §七.1 的决定行事，不是静默结束 |
| 8 | 验收真的验 | 故意让 worker 半途宣布完成 → `exit` 应该驳回或补做 |
| 9 | **成本对照** | 同一任务跑星型和 handoff，比三层用量。预期差 ~3%，若差异远大于此说明估算模型错了 |
| 10 | 前端渲染 | 平铺的 handoff 链在 ChatV2 里长什么样（§七.4） |

第 9 条的意义是**否证**：如果实测省的远超 3%，说明我对团长成本的拆解有问题，得回头查。

### 实测结果（七轮）

| # | 结果 |
|---|---|
| 1 | ✅ 两仓库 build / vet / test 干净（除 `tools/bash` 那条已知的 Windows 失败） |
| 2 | ✅ `TestBuildGivesEachModeExactlyItsOwnTool` —— 一个会话只持有一个工具 |
| 3 | ✅ 一个 `start`…`done`，库里一条 user + 一条 assistant |
| 4 | ✅ 产物在同一目录（`onboarding_review.md` + `slides/` + `.pptx` 全在） |
| 5 | ⚠️ **未单独验** —— 产物在同一 workspace 说明共享，但没去 Cube 数实例个数 |
| 6 | ✅ `entry → worker → exit` 顺序稳定，`chain_hop` 事件能看出来 |
| 7 | ✅ 断链兜底真机触发过（run4 调度员没交棒 → 送去验收员） |
| 8 | ⚠️ **未验** —— 没构造过"worker 半途宣布完成"的场景；验收员确实读了文件（解析 pptx 源码）但没被测试过它会驳回 |
| 9 | ✅ 见 §二末，**否证成功**：实测 +18%，不是省 3%。归因也错了（详见那节） |
| 10 | ❌ 未验，属 #193 |

**第 9 条那个否证是这轮最有价值的产出** —— 它不只推翻了数字，还推翻了归因：省的地方
（入口专家 −67%）和贵的地方（验收 +14k）都不在原先估的位置上。

**七轮里前六轮各抓到一个缺陷，全部是编译和单测拦不住的**：

| 轮 | 缺陷 |
|---|---|
| 1 | 预算耗尽走了错误路径，**验收专家从没跑** |
| 1 | 根因：`Expert` 没有迭代预算字段，专家跑在团长的 12 轮上（库里配的是 30） |
| 2 | leg 的 bridge id 传 `""`，**467k 子代理的归属丢了**（拍平缺陷第二次，换了个门） |
| 4 | 调度员**自己把活干了** —— 提示词里的禁令输给工具集里的手段 |
| 5 | 验收专家**转交给自己**，白烧 14k |
| 6 | 失败的那轮**不记账** —— 前面几棒的钱已经花了但 `token_count` 是空的 |

都已修 + 补测试 + 真机复核。这一栏是本文档最该被后来人读到的部分：**这类缺陷只有真机
能抓，且每一个都在"看起来跑通了"之后才暴露。**

---

## 九、不做的

- **B（专家互叫）** —— 每层一份底座，收益在 4% 里面。理由见 §三
- **C（圆桌群聊）** —— AutoGen GroupChat 那套共享历史，输入 token 随轮数平方增长。这正是我们刚拆出的 278k 的形状（70% 是底座重传）的放大版
- **省钱** —— 见 §二。要省去动 skill runner
- **预算耗尽自动交验收专家（salvage）** —— 实现过又删掉了。理由见 §七.1：验收专家看不见
  "这轮被切断了"，会把半成品当完整产出报告。主流产品都是停下等用户决定。**如果将来要
  重新做，必须先解决"怎么让验收专家知道自己在看一个不完整的产出"** —— 那需要一条它读得懂
  的信号，而不只是把 brief 措辞改一下
- **前端认 `chain_hop`** —— 事件播出去了，前端不处理只是看不见分棒。属 #193 重写范围

---

## 附：外部实现参考

| 框架 | 拓扑 | 专家间怎么传信息 |
|---|---|---|
| Anthropic Research | 星型 orchestrator-worker | 子代理**写文件**，只传轻量引用 |
| LangGraph Swarm | handoff（平级） | `Command(goto=..., graph=Command.PARENT)`，`active_agent` 存 state |
| OpenAI Agents SDK | 两种都有 | handoff = 转移控制权；agent-as-tool = 我们现在这套 |
| AutoGen Sequential | 流水线 | 只收上游那一条消息（最省） |
| AutoGen GroupChat | 圆桌 | 共享 `_chat_history`，每轮全量重传 |
| CrewAI hierarchical | 星型 + manager LLM | manager 转述，每次委派/复核各一次 LLM 调用 |

两个要注意的坑：

**LangGraph 和 OpenAI SDK 的 handoff 默认都传完整历史。** OpenAI 文档原话：「it's as though the new agent takes over the conversation, and gets to see **the entire previous conversation history**」。两家都专门提供了收窄的口子（OpenAI 的 `input_filter` / `remove_all_tools` / `nest_handoff_history`；LangGraph 是加 `task_description` 参数让模型自己写简报）。**我们必须只传 brief，默认值是陷阱。**

**Anthropic 明说 orchestrator-worker 不适合「需要共享上下文或大量 agent 间依赖」的场景** —— 不是那种场景该换拓扑，是那种场景就不该用多智能体。他们的数字：agent 用 ~4x 普通对话的 token，多智能体 ~15x，"only viable when task value justifies it"。

**第三个坑（本轮实测补充）：预算耗尽时别自动往下走。** 调研过的产品里没有一个这么做 ——
ChatGPT 撞上限直接结束会话；Copilot Workspace 呈现已完成的、标记未完成的、等用户点下一步；
Cursor 和 Claude Code 都是试着收尾然后停在那轮。共性是**用户在环**。我们一开始做了自动
交验收专家，实测发现它会把半成品报成完成，删掉了（§九）。

来源：
- [Anthropic — How we built our multi-agent research system](https://www.anthropic.com/engineering/built-multi-agent-research-system)
- [LangGraph Swarm](https://github.com/langchain-ai/langgraph-swarm-py)
- [OpenAI Agents SDK — Handoffs](https://openai.github.io/openai-agents-python/handoffs/)
- [AutoGen — Group Chat](https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/design-patterns/group-chat.html)
- [CrewAI — Processes](https://docs.crewai.com/concepts/processes)
