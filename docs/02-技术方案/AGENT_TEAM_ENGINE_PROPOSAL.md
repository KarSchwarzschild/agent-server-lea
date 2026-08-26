# agentloop 引擎修改：专家团（Agent Team）

> 状态：**已实现**（未提交，在 `D:\gitlab\agentloop` 工作区）
> 配套：`AGENT_TEAM_DEV_GUIDE.md`（lea-agent 侧怎么接）

目标：让一个专家（agent）拥有一级委派额度，把它绑定的多个 skill 的**执行过程**隔离到独立会话里。

原理：`Task` 拿不到是 `build.go` 的 append 顺序决定的（快照 → 之后才加 `Task`）。所以每调一次 `Build()`，新会话的主会话必然持有 `Task`。让专家跑在**独立的完整 Session** 里，它就自然能往下派：

```
团长 Session ──ask_expert──> 专家 Session（主会话，持有 Task）
                               └─ Task ──> skill runner 子会话
```

每个引擎会话内部仍只有一级委派，depth 规则没有被破坏。

---

## 一、实际改了什么

| 文件 | 改动 |
|---|---|
| `team/expert.go`（新，154 行） | `Expert` 类型 + `Set` + `Catalog` |
| `team/tool.go`（新，193 行） | `team.Tool`，名 `ask_expert`；`Runner` 接口 |
| `team/team_test.go`（新，274 行） | 15 条 |
| `expert_runner.go`（新，213 行） | `expertRunner` —— 派生 Config/Options、建会话、跑一轮、关掉 |
| `expert_runner_test.go`（新，289 行） | 8 条，含 Build 级递归护栏 |
| `internal/agentevents/`（新，143 行） | 事件桥从 `internal/subagent` 提出来，两个包共用 |
| `config.go` | 加 `Experts []team.Expert` |
| `build.go` | 注册 `ask_expert`（快照之后）+ 填 prompt catalog |
| `engine.go` | `Session.expertTool` + `SetExperts()` |
| `internal/prompt/assembler.go` | `AssemblerOpts.Experts` + 表里一行 |
| `internal/repl/session.go` | `expertsCatalog` + `SetExpertsCatalog` |
| `internal/subagent/launcher.go` | 切到 `agentevents`；子代理 prompt 清掉专家目录 |

### 1. `ask_expert` 与 `Task` 平级

```
subagent.Tool  →  "Task"        同一 Session 内起子会话
team.Tool      →  "ask_expert"  起一个独立的完整 Session
```

工具名由引擎统一 —— 子代理白名单、`DisabledTools`、prompt catalog 都按名字精确匹配。

### 2. `Expert.Tools` 是工具实例，不是名字白名单

这是与 `agent.Agent.Tools` 的关键区别：`subagent` 的子会话共用父会话的工具实例，只能靠名字过滤；专家会话是独立装配的完整会话，宿主直接给它该有的实例。

### 3. 两个工具属性

`IsConcurrencySafe() = true` —— 与 `Task` 一致。**并行还是串行由模型自己决定**，一次回复里发几个 `ask_expert` 就并行几个，不需要额外编排机制。

`ResultRetention()` 返回零值 —— 理由同 `Bash`：重跑一个专家既花真钱、结果也不相同。专家报告只能超长截断，不能因变旧被丢弃。这条**显式写了方法**而不是不实现，因为"不标 Reproducible"是个有意决定，值得留在代码里。

### 4. 递归护栏：宿主在类型上无法表达递归

`expertRunner.derive` 里 `cfg.Experts = nil`，在**引擎内部**、在宿主碰不到的值上。宿主没有任何入口把名册塞进专家会话。

第二重保险：`build.go` 里 `if expertSet.Len() > 0` 才注册，所以专家会话连 `ask_expert` 的 schema 都看不到。

`expert_runner_test.go:TestBuildRegistersAskExpertOnlyWithARoster` 走真实 `Build` 钉住这两条：团长会话有 `ask_expert`，专家会话没有、但有 `Task`。

### 5. 专家会话的 Config 派生

| 字段 | 取值 |
|---|---|
| `Experts` | **nil**（护栏） |
| `SessionID` | **继承团长的** |
| `PersistSession` / `Transcript` / `Resume` / `Continue` | 清空（专家会话是临时的） |
| `Model` | `Expert.Model`，空则继承 |
| `Skills` | `Expert.Skills` |
| `Agents` | **继承团长的**（专家要有 `Task` 才能往下派） |
| `SystemPrompt` | `renderExpertPersona(e)` |
| 其余 | 继承 |

`SessionID` 继承是硬要求：服务端文件工具靠 `tenantID + sessionID` 定位 workspace 和沙箱路由。继承 → 三层共享一个 workspace 一个沙箱。不继承 → 每个专家各起一个沙箱（冷启动实测 >10s），产物互不可见。

### 6. Options 派生

`ToolEvents` / `StreamDelta` 改道到事件桥（带 `ParentToolUseID` + `AgentName`）；`StreamThinking` / `LLMRequestObserver` / `StreamContextUsage` / `StreamRetry` 清空（都是团长会话作用域的）；`ExtraTools` 清空。

---

## 二、三处与原提案的偏离

### 偏离 1 · 没有 `resources.ExpertSessions`，`Close` 也不用级联

原提案说专家会话要挂进 `resources` 并让 `Session.Close` 级联。实现时发现不需要：专家会话的生命周期**恰好是一次 `ask_expert` 调用**，`expertRunner.Run` 里 `defer` 关掉，错误路径也走同一个 defer。挂进 `resources` 只会引入一个"谁先关"的竞态，而且要给 `resources` 加锁 —— 它现在是无锁的。

附带一个细节：关闭用 `context.WithoutCancel(ctx)` + 30s 超时。否则调用被取消时 `Close` 自己也立刻放弃，MCP 进程就泄漏了。

### 偏离 2 · `Expert.Tools` 走 `ToolRuntime` 包装，不走 `ExtraTools`

原提案没说清专家的工具怎么进会话。实现时这条是硬约束：`ExtraTools` 在 `build.go` 的子代理快照**之后**才 append，所以放那儿的工具**专家自己的子代理拿不到** —— 而"专家把 skill 派给子代理执行"正是整个设计的目的，子代理没有文件工具就等于派了活不给工具。

所以加了 `withExtraProvisioned(inner, extra)`，包一层 `ToolRuntime`，让专家的工具成为 `Provision` 结果的一部分，落在快照之前。

### 偏离 3 · 事件桥提取成 `internal/agentevents`

原提案说"建议提到 `internal/agentevents`"，已做。`internal/subagent/events.go` 删掉，两个 `TestEventBridge*` 测试跟着搬到新包（留在 `subagent` 里测别的包的东西是错的归属），顺手补了一条 `EmitUsage` 全零不发的测试。

---

## 三、原提案 §三 的 A/B：取了 A，B 仍然是个真实的选项

A（静态全量）与 B（目录 + 懒加载）的区别是**谁在什么时候做装配**：

- **A**：宿主在 `engine.Build()` 之前把每个专家的 `Tools` / `Skills` 填好。团挂 10 个专家就装 10 个，哪怕这轮只派到 2 个。
- **B**：引擎只拿 `Name` + `Description` 进 prompt；模型点名某个专家时，引擎回调 `Options.ExpertProvisioner(name)` 让宿主现装。只装用到的。

**`SetExperts()` 替代不了 B。** 它是推模型 —— 宿主主动换名册。但宿主**不知道模型什么时候会点名哪个专家**，那是模型在一轮对话中间决定的，没有钩子通知宿主。所以宿主没法"等到派活时再装"，只能全量预装，那就是 A。

### 取 A 的理由：成本被原提案高估了一个数量级

原提案说 A 会"白打 10 次 HTTP、白下 10 份 OBS bundle"。核实后：

- **lea-agent 不下载 OBS bundle。** `config_loader.go:27` 只拿 `BundleURI` 这个字符串；真正下包的是沙箱侧，在 `code_interpreter.go:156` 把 `{name, bundle_uri}` 交给 lea-tools，**执行时**才下。这个成本压根不在建会话路径上。
- **装配一个专家 = 一次内网 HTTP。** `internal.py` 的 `/internal/agent-config/{id}` 是一把查全（skills + kb_ids + tools + team + llm 一个响应）。同机房，几十毫秒，可并发。

所以 A 的真实开销是：团挂 10 个专家 → 建会话时多打 8 次内网 HTTP，约 0.3 秒。对一个动辄跑几分钟的专家团会话，不值得为此引入一个回调接口和一套失败语义。

### 另两条

**失败时机 A 更好。** A 在 `Build` 时就报错；B 到模型已经决定派活了才报，那时只能把错误当工具结果丢回给模型。

**将来要 B 加得起。** `Config.Experts` 的形状不变，只多一个 `Options.ExpertProvisioner` 和两个附带决定（provisioner 返错时跳过该专家还是 fail 会话；同一专家一轮内派两次要不要缓存实例）。不是推倒重来。

**触发条件**：如果实测发现一个团常挂 20+ 专家、或某个专家的装配需要额外的慢调用（比如按需生成 prompt），再做 B。

---

## 四、原提案 §四 两个小修：都没做

**1. `cfg.Agents != nil` 时内置 agent 不合并**（`build.go`）

后果没变：宿主一提供 roster，`researcher` 就消失，而 `skill/tool.go` 的 `delegationAdvice` 仍在劝模型委派给它。**这个是现存行为，本轮没碰** —— 它影响的是 lea-agent 要不要把 `researcher` 放进自己的 roster，是配置决定，不是引擎缺陷。记在开发指南 §四.1。

**2. `config.go` 提到的 `Skill.BundleBytes` 字段不存在**

`skill.Skill` 确实没有这个字段，全仓库只有 `config.go` 那一处注释提及（本轮那段注释是新加的 host-roster 文档，不是我写的）。后果：`hasBulkReferences` 走 `filepath.WalkDir` 扫真实目录，对服务端永远返回 false —— 我们的 skill 来自 DB/OBS，`Skill.Path` 指向沙箱内。

**没修**，因为修它要么加字段（改 `skill.Skill` 的形状，影响面超出本轮）、要么删注释（但那注释描述的意图可能是对的，只是字段还没做）。留给引擎负责人定。

---

## 五、验证状态

| # | 验什么 | 状态 |
|---|---|---|
| 1 | `go build ./...` | ✅ |
| 2 | `go vet ./...` | ✅ 无输出 |
| 3 | `gofmt` | ✅ |
| 4 | `go test ./...` | ✅ 除 `tools/bash` 的 `TestShellInvocationBash` |
| 5 | 递归护栏（Build 级） | ✅ `TestBuildRegistersAskExpertOnlyWithARoster` |
| 6 | 专家会话仍持有 `Task` | ✅ 同上 |
| 7 | `SessionID` 继承 | ✅ `TestDerivedExpertConfigInheritsSession…` |
| 8 | 专家工具走 Provision 通道 | ✅ `TestExpertToolsAreProvisionedNotAppended` |
| 9 | 事件归属改道 | ✅ `TestDerivedOptionsRerouteStreamingAndDropExtras` |
| 10 | `-race` | ❌ **未跑** —— 这台机器 `CGO_ENABLED=0`，`-race` 需要 cgo |
| 11 | 真机端到端 | ❌ **未做** —— 需要 lea-agent 侧 T4/T5 先接上 |

第 4 条那个失败改前就存在（`git stash` 后复现过）：`ShellName` 在 Git Bash 下拿到全路径 `C:\Program Files\Git\bin\bash.exe` 而测试期望 `bash`。是 Windows 环境问题，与本轮无关。

第 10、11 条是真实缺口，不是"应该没问题"。特别是 11 —— 按项目规则（SB-2 / H5.G 都踩过），没跑过真调用的集成不算完成。
