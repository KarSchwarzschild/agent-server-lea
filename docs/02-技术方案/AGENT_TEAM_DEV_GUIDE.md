# 专家团开发指南（服务端 lea-agent）

> 配套：`AGENT_TEAM_ENGINE_PROPOSAL.md`（引擎侧改动）
> 本文只讲引擎改完之后，lea-agent 这边怎么接。

---

## 零、进度

| 任务 | 状态 |
|---|---|
| 引擎侧（`team` 包 / `ask_expert` / `Config.Experts`） | ✅ 已实现，未提交 |
| T1 · `config_loader.go` 接 `team` 字段 | ✅ 已改 |
| T2 · skill 走引擎通路 | ⬜ 方案已定（见 Step 2），未动手 |
| T3 · 设 `MaxContextTokens` | ⬜ 未动手 |
| T4 · 接 `Config.Experts` + 专家装配 | ⬜ 引擎已就绪，可以开工 |
| T5 · 编排 prompt | ⬜ |
| T6 · 真机 E2E + 上下文实测 | ⬜ **这是判断方案值不值的唯一依据** |

---

## 一、现状：三个缺口

开工前必须知道，**专家团现在一次都没跑起来过**，原因不是编排逻辑缺失，是配置链断了三处。

### 1. `team` 字段被静默丢弃 —— ✅ 已修（T1）

> 以下是修之前的状态，保留作为记录。

`lea-config/api/internal.py:85-95` 老老实实把 team 和 members 查出来返回了：

```python
team_row = db.query(AgentTeam).filter_by(agent_id=agent_id).first()
if team_row:
    members = db.query(AgentTeamMember).filter_by(team_id=team_row.id).all()
    team = {"strategy": ..., "members": [_row_to_dict(m) for m in members]}
return {"agent": ..., "skills": ..., "tools": ..., "team": team, "llm": ...}
```

但 `internal/runtime/config_loader.go:74-83` 的接收结构体里**没有 `team`**：

```go
var raw struct {
    Agent  json.RawMessage `json:"agent"`
    KBIDs  []int           `json:"kb_ids"`
    Tools  []ToolDef       `json:"tools"`
    Skills []SkillDef      `json:"skills"`
    LLM    struct{ ... }   `json:"llm"`
    // team 没有 → json.Decode 静默丢弃，不报错
}
```

后果：`engine.Config.Agents` 永远是 nil → `build.go:225` 走 else 分支去磁盘找 → `CWD` 是 `/tmp`（`agent_session.go:106`）什么都没有 → **线上能委派的只有引擎内置六个**（general-purpose / code-reviewer / code-explorer / planner / researcher / debugger），配的专家一个都不在。

### 2. `Config.Skills` 是 nil，skill 走的不是引擎通路

`agent_session.go:92-98` 把 skill 目录拼成文字塞进 system prompt，让模型调 `activate_skill`：

```go
systemPrompt += "\n\n## 可用技能\n当用户请求匹配以下技能时，先调用 activate_skill 激活对应技能：\n"
```

而 `activate_skill` 走 `Options.ExtraTools` —— `build.go:271`，在子代理快照（`:222`）**之后**。所以任何子代理都拿不到它。

同时引擎自己的 `Skill` 工具（`build.go:185`，在快照**之前**）也注册着，但因为 `cfg.Skills` 是 nil，它的目录是空的，模型不会去调。

**两套并存，用的是拿不到的那套。**

### 3. `MaxContextTokens` 是 0，回收机制全程无效

`agent_session.go:100-110` 那个 `engine.Config{}` 里没有这个字段，默认 0。而 `repl/session.go:1094`：

```go
if s.maxContextTokens <= 0 {
    return working, false   // Reduce 整个不跑
}
```

后果：上一轮给五个文件工具写的 `ResultRetention`（`Reproducible` / `InputStub` / `KeepTail`）、引擎给 `Skill` 写的 `Reproducible` —— **全都在，一次没被调用过**。

---

## 二、施工顺序

前两步不依赖引擎改动，可以立刻做，且是任何方案的地基。

### Step 1 · 接上 `team` 字段 —— ✅ 已完成

`config_loader.go`：

```go
type TeamMemberDef struct {
    MemberAgentID int    `json:"member_agent_id"`
    Role          string `json:"role"`
    Name          string `json:"name"`
}

type TeamDef struct {
    Strategy          string          `json:"strategy"`
    DecomposerPrompt  string          `json:"decomposer_prompt"`
    SynthesizerPrompt string          `json:"synthesizer_prompt"`
    MaxSubtasks       int             `json:"max_subtasks"`
    Members           []TeamMemberDef `json:"members"`
}

// AgentConfig 加字段
Team *TeamDef `json:"team"`   // nil = 这个 agent 不是专家团
```

在 `raw` 结构体里加 `Team *TeamDef \`json:"team"\``，然后 `cfg.Team = raw.Team`。

字段名对齐 `models/team.py`：`strategy` / `decomposer_prompt` / `synthesizer_prompt` / `max_subtasks` / `member_agent_id` / `role` / `name`。

**验收**：对一个已配团的 agent 打 `/internal/agent-config/{id}`，Go 侧日志能打出 members 数量。

### Step 2 · 填 `Config.Skills`，退役 `activate_skill`

`agent_session.go`：

```go
engineCfg := engine.Config{
    // ...
    Skills:           toEngineSkills(cfg.Skills),
    MaxContextTokens: contextWindowFor(cfg.Model),
}
```

```go
// toEngineSkills 把 DB 里的 skill 转成引擎的 skill.Skill。
//
// Path 指向沙箱内的 bundle 目录而不是 lea-agent 本机路径：它只用于给模型显示
// "Skill directory: X"（skill/tool.go:250 locationHeader），模型据此去读
// 引用文件 —— 而模型读文件走的是我们的 Read 工具，落在沙箱/NFS 上。
// 填本机路径会让模型去读一个它访问不到的目录。
func toEngineSkills(defs []SkillDef) []skill.Skill {
    out := make([]skill.Skill, 0, len(defs))
    for _, d := range defs {
        out = append(out, skill.Skill{
            Name:        d.Name,
            Description: d.Description,
            Body:        d.SystemPrompt,
            Path:        sandboxSkillPath(d),   // 沙箱内路径
            Source:      skill.SourceUser,
        })
    }
    return out
}
```

同时删掉 `agent_session.go:92-98` 那段手拼的 `## 可用技能` —— 引擎的 `skill.Catalog`（`build.go:205`）会自动进 system prompt。

**注意 `nil` 和空 slice 语义不同**（`config.go:111-114`）：nil = "去扫磁盘"，空非 nil = "本会话没有"。没有 skill 时要给空 slice，不能给 nil，否则引擎会去扫 `/tmp`。

`activate_skill` 退役后 `platform_tools.go` 里对应的分支可以删。但**先跑通再删** —— 两套并存一段时间不会冲突。

**验收**：SSE 里出现 `tool_event{toolName:"Skill"}` 而不是 `activate_skill`；起一个子代理，它也能调 `Skill`。

### Step 3 · 设 `MaxContextTokens`

窗口数按模型定。建议做成一个映射函数而不是常量，因为不同专家可以用不同模型（`agents.model` 字段 + `internal.py:97-117` 已按 model 匹配 provider）：

```go
// contextWindowFor 返回模型的上下文预算。
//
// 取保守值：这个数字只用来决定"什么时候开始回收历史"
// （repl/session.go:1097 超过 threshold ratio 才跑 Reduce），
// 估小了只是回收得早一点，估大了会在真实上限前撞墙。
func contextWindowFor(model string) int { ... }
```

拿不准的模型给一个保守缺省（比如 128k）。

**验收**：跑一轮多工具调用的对话，telemetry 里出现 `EventHistoryReduced`，`BytesSaved > 0`。

### Step 4 · 接专家团

**引擎已改完**（见 `AGENT_TEAM_ENGINE_PROPOSAL.md`），API 定稿如下：

```go
// team.Expert —— 注意 Tools 是 []tool.Tool 实例，不是名字白名单
type Expert struct {
    Name        string
    Description string          // 进 system prompt 的目录项
    Prompt      string
    Model       string          // 空 = 继承团长
    Skills      []skill.Skill   // nil = 扫磁盘！服务端必须给非 nil
    Tools       []tool.Tool     // 工具实例
}

// 团长会话
engineCfg.Experts = experts              // []team.Expert
opts.SubagentLimiter = sharedLimiter     // 进程级总量，跨会话共享
```

专家装配 —— 用 `member_agent_id` 再走一次 `LoadAgentConfig`：

```go
func buildExpert(ctx context.Context, memberAgentID int, tenantID, userID, sessionID string) (team.Expert, error) {
    c, err := runtime.LoadAgentConfig(ctx, memberAgentID, tenantID)
    if err != nil {
        return team.Expert{}, err
    }
    return team.Expert{
        Name:        c.Name,
        Description: c.Description,
        Prompt:      c.SystemPrompt,
        Model:       c.Model,                    // 专家可用不同模型（便宜的干粗活）
        Skills:      toEngineSkills(c.Skills),   // 必须非 nil
        // sessionID 传团长的 → 专家写的文件落在同一个 workspace
        Tools: runtime.BuildPlatformTools(c, tenantID, userID, sessionID),
    }, nil
}
```

**三个坑，都会静默出错：**

1. **`sessionID` 必须传团长的**。这是三层共享 workspace 和沙箱的前提。传新的会让每个专家各起一个沙箱（冷启动 >10s）且产物互不可见。引擎侧 `SessionID` 是继承的，但 `BuildPlatformTools` 的 sessionID 是**你传的** —— 这里传错引擎管不了。

2. **`Skills` 不能是 nil**。nil 在引擎里意思是"去扫磁盘"，而 CWD 是 `/tmp`。没有 skill 时给 `[]skill.Skill{}`。

3. **文件工具不要放进 `Expert.Tools`**。它们由 `ToolRuntime` 为每个会话（包括专家会话）provision，重复放会撞名字。`Expert.Tools` 只放这个专家自己绑的平台工具（kb_search / code_interpreter 之类）。

**懒加载**：引擎不需要回调。`Expert.Tools` / `Skills` 都是宿主填的，想懒加载就先填空、真派到时用 `session.SetExperts(experts)` 换名册 —— 这个方法引擎已经给了。第一版不做。

### Step 5 · 编排 prompt

`strategy` 选模式，两段 prompt 各管一头：

```go
switch cfg.Team.Strategy {
case "pipeline":
    systemPrompt += cfg.Team.DecomposerPrompt   // 怎么分解
    systemPrompt += cfg.Team.SynthesizerPrompt  // 怎么汇总
case "autonomous":
    // 不注入，团长自己判断派谁、派几次
}
```

`MaxSubtasks` 也写进 prompt 当上限提示。

---

## 三、验证清单

必须真机端到端，不能只编译（SB-2 / H5.G 都踩过这个）。

| # | 验什么 | 怎么验 |
|---|---|---|
| 1 | 编译 + 单测 | `go build ./... && go vet ./... && go test ./internal/... -count=1` |
| 2 | team 字段到位 | 日志打出 members 数量 |
| 3 | skill 走引擎通路 | SSE 里是 `toolName:"Skill"` 不是 `activate_skill` |
| 4 | 子代理能用 skill | 起子代理让它加载 skill，SSE 有带 `parentToolUseID` 的 `Skill` 事件 |
| 5 | Reduce 生效 | telemetry `EventHistoryReduced`，`BytesSaved > 0` |
| 6 | 专家真的跑起来 | SSE 有 `ask_expert` 事件，专家的工具调用带 `ParentToolUseID` + `AgentName` |
| 7 | **workspace 共享** | 专家写文件 → 团长 `Read` 读得到 → 宿主 `ls {NFS_ROOT}/tenants/{tid}/sessions/{sid}/workspace` 确认在 |
| 8 | 沙箱只有一个 | 跑完一轮，确认 Cube 里只有一个 sandbox 实例（不是每个专家一个） |
| 9 | 不落库 | `chat_sessions` 表里没有专家的多余行 |
| 10 | 并行专家不打架 | 两个专家同时写同一路径 → 后写者报 409，不是静默覆盖 |
| 11 | **上下文实测** | 课程创作专家做 PPT + 配图，对比三层 vs 单层的峰值上下文和总 token |

第 11 条是这轮工作的目的，**不测就不知道三层方案值不值**。`launcher.go:218` 的子代理继承 `basePrompt`，而独立会话是 `Build` 从头组装 —— 工具 schema、skill 目录、环境信息三层各付一份。skill 过程小的时候可能倒亏。

---

## 四、已知坑

### 1. `researcher` 会消失

`build.go:218-221`：`cfg.Agents != nil` 时引擎**不合并内置六个**。而 `skill/tool.go:203` 的 `delegationAdvice` 在 skill bundle 超 64KB 时会往正文末尾追加：

```
delegate that reading to the "researcher" sub-agent via the Task tool
```

模型照做 → `unknown sub-agent "researcher"`。

**处理**：要么把 `researcher` 也放进我们的 roster（它专门读大量参考资料，对省上下文有实际价值），要么接受那句劝告失效。其余五个是代码场景人设，在我们产品里是噪音。

### 2. bulk-reference 建议对服务端无效

`hasBulkReferences`（`skill/tool.go:209`）走 `filepath.WalkDir` 扫 `Skill.Path` 的父目录 —— 我们的 skill 来自 DB/OBS，那个路径指向沙箱内，引擎本机没有那个目录，所以永远返回 false。

`config.go:119` 提到用 `Skill.BundleBytes` 解决这个，但**那个字段在 `skill.Skill` 里不存在**，全仓库只有那一处注释提及。已在提案 §四.2 里问引擎负责人。

### 3. prompt 固定成本按层数付

见验证清单第 11 条。

---

## 五、任务拆分

| 任务 | 依赖 | 说明 |
|---|---|---|
| T1 · `config_loader.go` 接 `team` | 无 | Step 1 |
| T2 · 填 `Config.Skills` + 退役 `activate_skill` | 无 | Step 2 |
| T3 · 设 `MaxContextTokens` | 无 | Step 3 |
| T4 · 接 `Config.Experts` + 专家装配 | 引擎改动 | Step 4 |
| T5 · 编排 prompt（pipeline / autonomous） | T4 | Step 5 |
| T6 · 真机 E2E + 上下文实测 | T5 | 验证清单全部 |
| T7 · 共享结论板（workspace `.team/findings.jsonl`） | T6 | 第二版，不需要引擎改动 |

T1–T3 可以现在就做，做完 skill 和回收机制立刻生效，与专家团是否落地无关。
