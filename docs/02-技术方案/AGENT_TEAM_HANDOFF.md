# 专家团第二形态：handoff（接力式）

> 状态：**设计中，未动手**
> 前置：`AGENT_TEAM_ENGINE_PROPOSAL.md`（引擎侧 `ask_expert`）、`AGENT_TEAM_DEV_GUIDE.md`（星型怎么接）
> 关联任务：#247（`strategy` / `role` 字段接上）

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

> ⚠️ `chat_messages.extra` 里的 `usage.agents[]` 是**拍平的五条，没有 parent** —— 只有 SSE 实时事件带
> `parentToolUseID`。所以从落库数据看不出成本归属，本文档第一版的拓扑图就是这么画错的（把 Task 画成
> 团长的直接子节点）。这条记在 `AGENT_TEAM_DEV_GUIDE.md` 的范围边界里（"collector 的子代理归属 → 下轮做"），
> 现在有了具体后果：**回看时无法判断成本归属**。

**团长只占 3.9%。** handoff 优化的就是这一层，而且不是全省 —— 总得有人接用户消息、决定第一棒（见 §四）。加上头尾专家之后：

```
现在:  团长 16,250（3 轮 × ~5,400）
handoff: 头专家 ~1,500 + 尾专家 ~3,000 = ~4,500
```

省 **~2.8%**，且是估算。

那 278,085 是跑 `pptx-generator` 的 skill runner，上一轮已拆明：起始底座 11,452、每轮增长 613，`11,452 × 17 = 194,682 = 70%` 是底座重传。**省钱要动那里（#232 / #248），不在本文档范围。**

> 结论：handoff 的价值是**编排能力**（专家自主接力 + 专职验收），不是成本。任何把它当省钱项汇报的说法都不成立。

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

---

## 七、待决

### 1 · 链断了怎么办（必须定）

专家不填 `next`、或填了不存在的专家名。三个选项：

- 兜给 `exit` 专家，让它判断能不能收尾
- 报错终止，SSE 发 error
- 退回 `entry` 重新选

**倾向第一个** —— 已经有验收专家了，让它兼任兜底最自然，且不引入新角色。

### 2 · 用户追问谁接

handoff 模式下最后说话的是 `exit` 专家，它不认识用户、也没有对话历史。选项：

- 下一轮从 `entry` 重新开始（简单，但丢上下文）
- 记住 active expert，追问直接给它（要存状态；我们 session 绑单 pod，进程内就够，不需要 Redis）

服务端每轮重建会话，倾向第一个。桌面长驻对话可能要第二个 —— 这正是「宿主驱动」的价值：两边各自决定。

### 3 · `next` 协议的形状

结构化标记（`<next expert="..." brief="..."/>`）还是工具调用（`handoff_to`）？

**倾向工具调用**：schema 能约束、填错名字引擎能报可纠正的 `is_error`（照 `team/tool.go:160` 的 `unknown expert` 那条），而标记要自己写解析和容错。

### 4 · 前端

SSE 事件复用 `ask_expert` 那套（`agentName` 带专家名），前端不用改。但 handoff 链的可视化是**平铺**而不是嵌套 —— 现在 collector 把子会话事件按 `ParentToolUseID` 嵌套（`collector.go:85`），handoff 没有父子关系。**未验证前端怎么渲染。**

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

---

## 九、不做的

- **B（专家互叫）** —— 每层一份底座，收益在 4% 里面。理由见 §三
- **C（圆桌群聊）** —— AutoGen GroupChat 那套共享历史，输入 token 随轮数平方增长。这正是我们刚拆出的 278k 的形状（70% 是底座重传）的放大版
- **省钱** —— 见 §二。要省去动 skill runner

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

来源：
- [Anthropic — How we built our multi-agent research system](https://www.anthropic.com/engineering/built-multi-agent-research-system)
- [LangGraph Swarm](https://github.com/langchain-ai/langgraph-swarm-py)
- [OpenAI Agents SDK — Handoffs](https://openai.github.io/openai-agents-python/handoffs/)
- [AutoGen — Group Chat](https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/design-patterns/group-chat.html)
- [CrewAI — Processes](https://docs.crewai.com/concepts/processes)
